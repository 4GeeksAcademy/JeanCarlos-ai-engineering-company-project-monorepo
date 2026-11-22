"""The LangGraph agent with stubbed RAG functions: routing, compile-time validation,
checkpointing and resume, and the persisted trace.

    python -m pytest tests/pipelines/test_agent_graph.py
"""

import pytest
from langgraph.graph import END, START, StateGraph

from data.pipelines import rag
from services.agent import graph as agent_graph
from services.agent import nodes
from services.agent.graph import GraphStructureError, compile_agent, validate_structure
from services.agent.runner import AgentRejected, AgentRunError, AgentRuntime
from services.agent.state import AgentState

CHUNK = {"company": "healthcore", "source_document": "appointment-policy", "section": "Política de cancelación",
         "language": "es", "chunk_index": 1, "text": "- Cancelar con más de 24 horas: sin cargo.", "score": 0.61}


@pytest.fixture
def rag_calls(monkeypatch):
    """Stub retrieve() and generate_answer() of data/pipelines/rag.py, recording the calls."""
    calls = []

    def retrieve(question):
        calls.append(("retrieve", question))
        return [CHUNK] if "cancel" in question.lower() else []

    def generate(question, context):
        calls.append(("generate", question, context))
        return "Si cancela con más de 24 horas no hay cargo.\nFuente: Política de Citas y Cancelaciones — Política de cancelación"

    monkeypatch.setattr(rag, "retrieve", retrieve)
    monkeypatch.setattr(rag, "generate_answer", generate)
    return calls


@pytest.fixture
def runtime(tmp_path):
    return AgentRuntime(tmp_path / "agent.sqlite")


# ---------------------------------------------------------------- routing


def test_answerable_question_goes_receive_retrieve_generate(runtime, rag_calls):
    result = runtime.run("  ¿Cobran por cancelar   con 12 horas?  ")

    assert result.answer.startswith("Si cancela con más de 24 horas")
    assert runtime.trace(result.run_id)["route"] == "receive_question > retrieve_context > generate_answer"
    # retrieve once; generation receives exactly the chunks retrieve produced (no second retrieval)
    assert rag_calls == [("retrieve", "¿Cobran por cancelar con 12 horas?"),
                         ("generate", "¿Cobran por cancelar con 12 horas?", [CHUNK])]


def test_no_context_routes_to_the_honest_answer_without_calling_the_model(runtime, rag_calls):
    result = runtime.run("¿Cuál es la receta de la paella?")
    assert result.answer == nodes.NO_INFORMATION_ANSWER
    assert runtime.trace(result.run_id)["route"] == "receive_question > retrieve_context > answer_no_information"
    assert [call[0] for call in rag_calls] == ["retrieve"]


@pytest.mark.parametrize("question", ["", "   ", "ok", "x" * 1001])
def test_invalid_question_is_rejected_before_retrieval(runtime, rag_calls, question):
    with pytest.raises(AgentRejected) as rejected:
        runtime.run(question)
    trace = runtime.trace(rejected.value.run_id)
    assert trace["route"] == "receive_question > reject_question" and trace["status"] == "rejected"
    assert rag_calls == []


def test_routing_functions_read_only_the_state():
    assert agent_graph.route_after_receive({"status": "received"}) == "retrieve_context"
    assert agent_graph.route_after_receive({"status": "rejected"}) == "reject_question"
    assert agent_graph.route_after_retrieve({"context": [CHUNK]}) == "generate_answer"
    assert agent_graph.route_after_retrieve({"context": []}) == "answer_no_information"


def test_no_node_calls_the_monolithic_query(runtime, rag_calls, monkeypatch):
    monkeypatch.setattr(rag, "query", lambda question: pytest.fail("query() must not be called by the graph"))
    runtime.run("¿Cobran por cancelar?")


# ---------------------------------------------------------------- compile-time validation


def test_the_real_graph_compiles_at_import():
    assert agent_graph.STRUCTURE is not None
    drawn = agent_graph.STRUCTURE.get_graph()
    assert {"receive_question", "retrieve_context", "generate_answer", "answer_no_information",
            "reject_question"} <= set(drawn.nodes)


def _graph(extra_node=None, edge_to=None, branch_target=None):
    graph = StateGraph(AgentState)
    graph.add_node("receive_question", lambda s: {})
    graph.add_node("generate_answer", lambda s: {})
    graph.add_edge(START, "receive_question")
    if branch_target:
        graph.add_conditional_edges("receive_question", lambda s: "x", {"x": branch_target})
    else:
        graph.add_edge("receive_question", "generate_answer")
    graph.add_edge("generate_answer", END)
    if extra_node:
        graph.add_node(extra_node, lambda s: {})
        if edge_to:
            graph.add_edge("generate_answer", extra_node)
    return graph


def test_compile_fails_on_a_node_not_connected_to_start():
    with pytest.raises(GraphStructureError, match="not connected to START: \\['orphan'\\]"):
        validate_structure(_graph(extra_node="orphan"), {"receive_question": frozenset(), "generate_answer": frozenset(),
                                                         "orphan": frozenset()})


def test_compile_fails_on_a_dead_end_node():
    with pytest.raises(GraphStructureError, match="no path to END: \\['dead_end'\\]"):
        validate_structure(_graph(extra_node="dead_end", edge_to=True),
                           {"receive_question": frozenset(), "generate_answer": frozenset(), "dead_end": frozenset()})


def test_compile_fails_on_a_conditional_route_to_an_unknown_node():
    with pytest.raises(GraphStructureError, match="unknown node 'missing'"):
        validate_structure(_graph(branch_target="missing"), {"receive_question": frozenset(), "generate_answer": frozenset()})


def test_compile_fails_when_a_node_writes_keys_outside_the_state(monkeypatch):
    monkeypatch.setitem(nodes.NODE_OUTPUTS, "generate_answer", frozenset({"answer", "chat_history"}))
    with pytest.raises(GraphStructureError, match="not in AgentState: \\['chat_history'\\]"):
        compile_agent()


def test_compile_fails_when_a_node_is_missing(monkeypatch):
    broken = {name: fn for name, fn in agent_graph.NODES.items() if name != "answer_no_information"}
    with pytest.raises(GraphStructureError, match="answer_no_information"):
        compile_agent(node_fns=broken)


# ---------------------------------------------------------------- checkpointing and trace


def test_a_checkpoint_is_written_after_every_node(runtime, rag_calls):
    result = runtime.run("¿Cobran por cancelar?")
    checkpoints = runtime.checkpoint_history(result.run_id)
    assert [c["next"] for c in checkpoints] == [["__start__"], ["receive_question"], ["retrieve_context"],
                                                ["generate_answer"], []]
    assert checkpoints[3]["status"] == "retrieved" and "context" in checkpoints[3]["state_keys"]
    assert set(checkpoints[-1]["state_keys"]) == {"question", "status", "context", "answer"}


def test_failed_run_is_traced_and_resumes_from_its_last_checkpoint(tmp_path, rag_calls, monkeypatch):
    database = tmp_path / "agent.sqlite"
    runtime = AgentRuntime(database)

    def gateway_down(question, context):
        raise ConnectionError("gateway timeout")

    monkeypatch.setattr(rag, "generate_answer", gateway_down)
    with pytest.raises(AgentRunError) as failure:
        runtime.run("¿Cobran por cancelar?")
    run_id = failure.value.run_id
    assert failure.value.node == "generate_answer"
    trace = runtime.trace(run_id)
    assert trace["status"] == "failed" and trace["steps"][-1]["status"] == "error"
    assert trace["steps"][-1]["error"] == "ConnectionError: gateway timeout"

    # the gateway is back; a new process (same database) resumes the run without re-retrieving
    monkeypatch.setattr(rag, "generate_answer", lambda question, context: "Sin cargo.\nFuente: X")
    retrieves_before = [c for c in rag_calls if c[0] == "retrieve"]
    resumed = AgentRuntime(database).resume(run_id)

    assert resumed.answer == "Sin cargo.\nFuente: X"
    assert [c for c in rag_calls if c[0] == "retrieve"] == retrieves_before  # retrieve did not run again
    final = AgentRuntime(database).trace(run_id)
    assert final["status"] == "answered"
    assert [s["node"] for s in final["steps"]] == ["receive_question", "retrieve_context", "generate_answer",
                                                   "generate_answer"]


def test_trace_is_persisted_and_queryable_after_the_run(tmp_path, rag_calls):
    database = tmp_path / "agent.sqlite"
    run_id = AgentRuntime(database).run("¿Cobran por cancelar?").run_id

    trace = AgentRuntime(database).trace(run_id)  # another process, same file
    assert trace["question"] == "¿Cobran por cancelar?" and trace["status"] == "answered"
    retrieve_step = trace["steps"][1]
    assert retrieve_step["node"] == "retrieve_context" and retrieve_step["duration_ms"] >= 0
    assert retrieve_step["output"]["context"] == [
        {"source_document": "appointment-policy", "chunk_index": 1, "section": "Política de cancelación", "score": 0.61}]
