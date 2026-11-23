"""The LangGraph agent with stubbed sources: routing between the RAG and the tools,
fallbacks, compile-time validation, checkpointing and resume, and the persisted trace.

    python -m pytest tests/pipelines/test_agent_graph.py
"""

import pytest
from langgraph.graph import END, START, StateGraph

from data.pipelines import rag
from services.agent import graph as agent_graph
from services.agent import nodes, planner, tools
from services.agent.graph import GraphStructureError, compile_agent, validate_structure
from services.agent.planner import SourcePlan
from services.agent.runner import AgentRejected, AgentRunError, AgentRuntime
from services.agent.state import AgentState

CHUNK = {"company": "healthcore", "source_document": "appointment-policy", "section": "Política de cancelación",
         "language": "es", "chunk_index": 1, "text": "- Cancelar con más de 24 horas: sin cargo.", "score": 0.61}
TICKET = {"id": 2, "title": "Patient not informed of appointment reschedule by clinic", "status": "open",
          "category": "patient_experience", "origin": "customer", "branch": "london_west",
          "created_at": "2025-03-12T09:00:00Z", "updated_at": "2025-03-14T10:30:00Z"}
PRODUCT = {"id": 1, "name": "Guantes de nitrilo (caja de 100)", "sku": "HCR-PPE-001", "category": "ppe",
           "unit": "box", "country": "US", "current_stock": 102}


@pytest.fixture
def sources(monkeypatch):
    """Stub every source: the RAG functions, the planner (rules only) and the tool calls."""
    calls = []

    def retrieve(question):
        calls.append(("retrieve", question))
        return [CHUNK] if "cancel" in question.lower() else []

    def generate(question, context):
        calls.append(("generate", question, context))
        return "Si cancela con más de 24 horas no hay cargo.\nFuente: Política de Citas y Cancelaciones — Política de cancelación"

    def lookup_tickets(query):
        calls.append(("tickets", query.ticket_id))
        if query.ticket_id == 404:
            return tools.TicketLookupResult(status="not_found", query=query, endpoint="MCP get_incident",
                                            duration_ms=3, error="ticket not found")
        if query.ticket_id == 503:
            return tools.TicketLookupResult(status="unavailable", query=query, endpoint="MCP get_incident",
                                            duration_ms=4000, error="timeout after 4 s")
        return tools.TicketLookupResult(status="ok", query=query, endpoint="MCP get_incident",
                                        duration_ms=12, tickets=[tools.Ticket.model_validate(TICKET)], total=1)

    def lookup_stock(query):
        calls.append(("stock", query.product))
        if "desfibrilador" in query.product:
            return tools.StockLookupResult(status="not_found", query=query, endpoint="MCP query_inventory",
                                           duration_ms=8, error="no product matches")
        return tools.StockLookupResult(status="ok", query=query, endpoint="MCP query_inventory", duration_ms=9,
                                       products=[tools.ProductStock.model_validate(PRODUCT)])

    monkeypatch.setattr(rag, "retrieve", retrieve)
    monkeypatch.setattr(rag, "generate_answer", generate)
    monkeypatch.setattr(planner, "plan_with_model", lambda q: (_ for _ in ()).throw(RuntimeError("no model")))
    monkeypatch.setattr(tools, "lookup_tickets", lookup_tickets)
    monkeypatch.setattr(tools, "lookup_stock", lookup_stock)
    return calls


@pytest.fixture
def runtime(tmp_path):
    return AgentRuntime(tmp_path / "agent.sqlite")


def route(runtime, run_id):
    return runtime.trace(run_id)["route"].split(" > ")


# ---------------------------------------------------------------- routing RAG / tools


def test_policy_question_uses_only_the_knowledge_base(runtime, sources):
    result = runtime.run("  ¿Cobran por cancelar   con 12 horas?  ")

    assert route(runtime, result.run_id) == ["receive_question", "plan_sources", "retrieve_context",
                                             "generate_answer", "compose_answer"]
    assert runtime.trace(result.run_id)["sources"] == "knowledge_base"
    assert result.answer.startswith("Si cancela con más de 24 horas")
    # retrieve once; generation receives exactly the chunks retrieve produced
    assert sources == [("retrieve", "¿Cobran por cancelar con 12 horas?"),
                       ("generate", "¿Cobran por cancelar con 12 horas?", [CHUNK])]


def test_ticket_question_uses_only_the_incident_tool(runtime, sources):
    result = runtime.run("¿En qué estado está el ticket 2?")

    assert route(runtime, result.run_id) == ["receive_question", "plan_sources", "lookup_tickets", "compose_answer"]
    assert runtime.trace(result.run_id)["sources"] == "incidents"
    assert sources == [("tickets", 2)]  # no retrieval, no generation model
    assert "El ticket #2" in result.answer and "En progreso" not in result.answer and "Abierto" in result.answer
    assert "Fuente: gestor de incidentes vía servidor MCP (MCP get_incident)" in result.answer


def test_stock_question_uses_only_the_inventory_tool(runtime, sources):
    result = runtime.run("¿Tenemos stock de guantes de nitrilo?")
    assert route(runtime, result.run_id) == ["receive_question", "plan_sources", "lookup_stock", "compose_answer"]
    assert runtime.trace(result.run_id)["sources"] == "inventory"
    assert "102 cajas" in result.answer


def test_mixed_question_uses_the_tool_and_then_the_knowledge_base(runtime, sources):
    result = runtime.run("¿En qué estado está el ticket 2 y qué dice la política de cancelación?")

    assert route(runtime, result.run_id) == ["receive_question", "plan_sources", "lookup_tickets",
                                             "retrieve_context", "generate_answer", "compose_answer"]
    assert runtime.trace(result.run_id)["sources"] == "incidents > knowledge_base"
    ticket_part, rag_part = result.answer.split("\n\n", 2)[0], result.answer
    assert ticket_part.startswith("El ticket #2") and "Si cancela con más de 24 horas" in rag_part


def test_no_context_routes_to_the_honest_answer_without_calling_the_model(runtime, sources):
    result = runtime.run("¿Cuál es la receta de la paella?")
    assert result.answer == nodes.NO_INFORMATION_ANSWER
    assert route(runtime, result.run_id)[2:] == ["retrieve_context", "answer_no_information", "compose_answer"]
    assert [call[0] for call in sources] == ["retrieve"]


# ---------------------------------------------------------------- fallbacks


@pytest.mark.parametrize("ticket_id, expected", [
    (404, "No encontré el ticket #404"),
    (503, "No pude confirmar el estado del ticket #503 ahora mismo"),
])
def test_ticket_failures_route_to_the_fallback_and_never_invent_a_status(runtime, sources, ticket_id, expected):
    result = runtime.run(f"¿En qué estado está el ticket {ticket_id}?")

    assert route(runtime, result.run_id) == ["receive_question", "plan_sources", "lookup_tickets",
                                             "ticket_fallback", "compose_answer"]
    assert result.answer.startswith(expected)
    assert not any(label in result.answer for label in nodes.STATUS_LABELS.values())
    assert runtime.trace(result.run_id)["status"] == "answered"  # the run did not break


def test_stock_fallback_for_a_product_that_is_not_in_the_inventory(runtime, sources):
    result = runtime.run("¿Hay stock de desfibriladores?")
    assert route(runtime, result.run_id)[2:] == ["lookup_stock", "stock_fallback", "compose_answer"]
    assert "No encontré «desfibriladores» en el inventario" in result.answer


def test_a_failed_tool_does_not_stop_the_knowledge_base_part(runtime, sources):
    result = runtime.run("¿En qué estado está el ticket 503 y qué dice la política de cancelación?")
    assert route(runtime, result.run_id)[2:] == ["lookup_tickets", "ticket_fallback", "retrieve_context",
                                                 "generate_answer", "compose_answer"]
    assert result.answer.startswith("No pude confirmar el estado del ticket #503")
    assert "Si cancela con más de 24 horas" in result.answer


@pytest.mark.parametrize("question", ["", "   ", "ok", "x" * 1001])
def test_invalid_question_is_rejected_before_any_source(runtime, sources, question):
    with pytest.raises(AgentRejected) as rejected:
        runtime.run(question)
    trace = runtime.trace(rejected.value.run_id)
    assert trace["route"] == "receive_question > reject_question" and trace["status"] == "rejected"
    assert sources == [] and trace["sources"] is None


# ---------------------------------------------------------------- routing functions


def test_next_source_reads_only_the_plan_and_what_already_ran():
    plan = {"knowledge_base": True, "ticket": {"ticket_id": 2}, "inventory": {"product": "guantes"}}
    assert agent_graph.next_source({"plan": plan}) == "lookup_tickets"
    assert agent_graph.next_source({"plan": plan, "ticket_result": {}}) == "lookup_stock"
    assert agent_graph.next_source({"plan": plan, "ticket_result": {}, "stock_result": {}}) == "retrieve_context"
    assert agent_graph.next_source({"plan": plan, "ticket_result": {}, "stock_result": {}, "context": []}) == "compose_answer"
    assert agent_graph.route_after_receive({"status": "rejected"}) == "reject_question"
    assert agent_graph.route_after_tickets({"plan": plan, "ticket_result": {"status": "unavailable"}}) == "ticket_fallback"
    assert agent_graph.route_after_retrieve({"context": []}) == "answer_no_information"


def test_no_node_calls_the_monolithic_query(runtime, sources, monkeypatch):
    monkeypatch.setattr(rag, "query", lambda question: pytest.fail("query() must not be called by the graph"))
    runtime.run("¿Cobran por cancelar?")


# ---------------------------------------------------------------- planner


def test_rules_planner_finds_tickets_stock_and_policy():
    assert planner.plan_with_rules("¿En qué estado está el ticket #482?").ticket.ticket_id == 482
    assert planner.plan_with_rules("¿Ya resolvieron la incidencia número 92?").ticket.ticket_id == 92
    listing = planner.plan_with_rules("¿Qué tickets abiertos hay en London West?")
    assert listing.ticket.status == "open" and listing.ticket.ticket_id is None and not listing.knowledge_base
    assert planner.plan_with_rules("¿Tenemos stock de guantes de nitrilo?").inventory.product == "guantes de nitrilo"
    policy = planner.plan_with_rules("¿Cobran cargo por cancelación con 12 horas?")
    assert policy.knowledge_base and policy.ticket is None and policy.inventory is None
    both = planner.plan_with_rules("¿En qué estado está el ticket 2 y qué dice la política de cancelación?")
    assert both.ticket.ticket_id == 2 and both.knowledge_base


class _FakeChat:
    def __init__(self, content):
        self.completions = self
        self.content = content

    def create(self, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def _fake_model(monkeypatch, content):
    from types import SimpleNamespace
    monkeypatch.setattr(planner, "llm_client", lambda: SimpleNamespace(chat=_FakeChat(content)))
    monkeypatch.setattr(planner, "settings", lambda: SimpleNamespace(generation_model="m"))


def test_model_plan_is_validated_against_the_tool_contracts(monkeypatch):
    _fake_model(monkeypatch, '```json\n{"knowledge_base": false, "ticket": {"ticket_id": 2, "status": null}, "inventory": null}\n```')
    plan = planner.plan_sources("¿En qué estado está el ticket 2?")
    assert plan.planner == "model" and plan.ticket.ticket_id == 2 and not plan.knowledge_base


def test_an_invented_ticket_id_is_rejected_and_the_rules_decide(monkeypatch):
    _fake_model(monkeypatch, '{"knowledge_base": false, "ticket": {"ticket_id": 482}, "inventory": null}')
    plan = planner.plan_sources("¿Cuántos tickets hay en total en el sistema?")
    assert plan.planner == "rules" and "482" in plan.note
    assert plan.ticket is None


def test_a_value_outside_the_incident_catalog_falls_back_to_the_rules(monkeypatch):
    _fake_model(monkeypatch, '{"knowledge_base": false, "ticket": {"status": "closed"}, "inventory": null}')
    plan = planner.plan_sources("¿Qué tickets cerrados hay?")
    assert plan.planner == "rules" and plan.ticket.status == "resolved"


def test_a_plan_with_no_source_uses_the_knowledge_base(monkeypatch):
    _fake_model(monkeypatch, '{"knowledge_base": false, "ticket": null, "inventory": null}')
    assert planner.plan_sources("¿Hola?").knowledge_base is True


# ---------------------------------------------------------------- compile-time validation


def test_the_real_graph_compiles_at_import():
    drawn = agent_graph.STRUCTURE.get_graph()
    assert set(agent_graph.NODES) <= set(drawn.nodes)


def _graph(extra_node=None, edge_to=None, branch_target=None):
    graph = StateGraph(AgentState)
    graph.add_node("receive_question", lambda s: {})
    graph.add_node("compose_answer", lambda s: {})
    graph.add_edge(START, "receive_question")
    if branch_target:
        graph.add_conditional_edges("receive_question", lambda s: "x", {"x": branch_target})
    else:
        graph.add_edge("receive_question", "compose_answer")
    graph.add_edge("compose_answer", END)
    if extra_node:
        graph.add_node(extra_node, lambda s: {})
        if edge_to:
            graph.add_edge("compose_answer", extra_node)
    return graph


OUTPUTS = {"receive_question": frozenset(), "compose_answer": frozenset()}


def test_compile_fails_on_a_node_not_connected_to_start():
    with pytest.raises(GraphStructureError, match="not connected to START: \\['orphan'\\]"):
        validate_structure(_graph(extra_node="orphan"), {**OUTPUTS, "orphan": frozenset()})


def test_compile_fails_on_a_dead_end_node():
    with pytest.raises(GraphStructureError, match="no path to END: \\['dead_end'\\]"):
        validate_structure(_graph(extra_node="dead_end", edge_to=True), {**OUTPUTS, "dead_end": frozenset()})


def test_compile_fails_on_a_conditional_route_to_an_unknown_node():
    with pytest.raises(GraphStructureError, match="unknown node 'missing'"):
        validate_structure(_graph(branch_target="missing"), OUTPUTS)


def test_compile_fails_when_a_node_writes_keys_outside_the_state(monkeypatch):
    monkeypatch.setitem(nodes.NODE_OUTPUTS, "lookup_tickets", frozenset({"ticket_result", "auth_token"}))
    with pytest.raises(GraphStructureError, match="not in AgentState: \\['auth_token'\\]"):
        compile_agent()


def test_compile_fails_when_a_node_is_missing():
    broken = {name: fn for name, fn in agent_graph.NODES.items() if name != "ticket_fallback"}
    with pytest.raises(GraphStructureError, match="ticket_fallback"):
        compile_agent(node_fns=broken)


# ---------------------------------------------------------------- checkpointing and trace


def test_a_checkpoint_is_written_after_every_node(runtime, sources):
    result = runtime.run("¿En qué estado está el ticket 2 y qué dice la política de cancelación?")
    checkpoints = runtime.checkpoint_history(result.run_id)
    assert [c["next"] for c in checkpoints] == [["__start__"], ["receive_question"], ["plan_sources"],
                                                ["lookup_tickets"], ["retrieve_context"], ["generate_answer"],
                                                ["compose_answer"], []]
    assert "ticket_result" in checkpoints[4]["state_keys"]


def test_failed_run_is_traced_and_resumes_from_its_last_checkpoint(tmp_path, sources, monkeypatch):
    database = tmp_path / "agent.sqlite"
    runtime = AgentRuntime(database)

    def gateway_down(question, context):
        raise ConnectionError("gateway timeout")

    monkeypatch.setattr(rag, "generate_answer", gateway_down)
    with pytest.raises(AgentRunError) as failure:
        runtime.run("¿En qué estado está el ticket 2 y qué dice la política de cancelación?")
    run_id = failure.value.run_id
    assert failure.value.node == "generate_answer"
    trace = runtime.trace(run_id)
    assert trace["status"] == "failed" and trace["steps"][-1]["error"] == "ConnectionError: gateway timeout"

    monkeypatch.setattr(rag, "generate_answer", lambda question, context: "Sin cargo.\nFuente: X")
    done_before = list(sources)
    resumed = AgentRuntime(database).resume(run_id)

    assert resumed.answer.endswith("Sin cargo.\nFuente: X") and resumed.answer.startswith("El ticket #2")
    assert sources == done_before  # neither the ticket tool nor retrieve ran again
    final = AgentRuntime(database).trace(run_id)
    assert final["status"] == "answered" and final["sources"] == "incidents > knowledge_base"


def test_trace_is_persisted_and_shows_the_tool_output(tmp_path, sources):
    database = tmp_path / "agent.sqlite"
    run_id = AgentRuntime(database).run("¿En qué estado está el ticket 2?").run_id

    trace = AgentRuntime(database).trace(run_id)  # another process, same file
    plan_step, tool_step = trace["steps"][1], trace["steps"][2]
    assert plan_step["output"]["plan"]["ticket"]["ticket_id"] == 2 and plan_step["output"]["plan"]["planner"] == "rules"
    assert tool_step["node"] == "lookup_tickets"
    assert tool_step["output"]["ticket_result"]["status"] == "ok"
    assert tool_step["output"]["ticket_result"]["tickets"][0]["status"] == "open"


def test_mixed_questions_send_only_the_policy_part_to_the_rag(runtime, sources, monkeypatch):
    plan = SourcePlan(knowledge_base=True, knowledge_question="¿Qué dice la política de cancelación?",
                      ticket=tools.TicketQuery(ticket_id=2), planner="model")
    monkeypatch.setattr(planner, "plan_with_model", lambda q: plan)
    runtime.run("El ticket 2 es de un paciente al que no avisaron del cambio: ¿estado y política de cancelación?")
    assert [c[1] for c in sources if c[0] in ("retrieve", "generate")] == ["¿Qué dice la política de cancelación?"] * 2


def test_knowledge_question_is_dropped_when_no_tool_is_used(monkeypatch):
    _fake_model(monkeypatch, '{"knowledge_base": true, "knowledge_question": "otra cosa", "ticket": null, "inventory": null}')
    assert planner.plan_sources("¿Cobran por cancelar?").knowledge_question is None
