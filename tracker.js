const API_BASE = "https://playground.4geeks.com/tracker/api/v1";
const PAGE_SIZE = 1000;

const STATUS_LABELS = {
	received: "Recibida",
	in_progress: "En proceso",
	selected: "Seleccionada",
	discarded: "Descartada",
};

const STAGE_LABELS = {
	pending: "Pendiente",
	review: "Revisión",
	personal_interview: "Entrevista personal",
	technical_interview: "Entrevista técnica",
	offer_presented: "Oferta presentada",
};

const state = {
	records: [],
	visibleRecords: [],
	selectedRecord: null,
	selectedNotes: [],
	searchTimeout: null,
};

const elements = {
	list: document.querySelector("#candidate-list"),
	tableBody: document.querySelector("#candidate-list tbody"),
	loading: document.querySelector("#loading-state"),
	empty: document.querySelector("#empty-state"),
	error: document.querySelector("#error-state"),
	errorMessage: document.querySelector("#error-message"),
	count: document.querySelector("#results-count"),
	search: document.querySelector("#search-input"),
	status: document.querySelector("#status-filter"),
	stage: document.querySelector("#stage-filter"),
	detailDialog: document.querySelector("#detail-dialog"),
	candidateDialog: document.querySelector("#candidate-dialog"),
	toast: document.querySelector("#toast"),
};

async function request(path, options = {}) {
	const response = await fetch(`${API_BASE}${path}`, {
		...options,
		headers: {
			...(options.body ? { "Content-Type": "application/json" } : {}),
			...options.headers,
		},
	});

	if (!response.ok) {
		let detail = "";
		try {
			const body = await response.json();
			detail = body.detail || body.message || "";
		} catch {
			detail = "";
		}
		throw new Error(detail || `La solicitud falló (${response.status}).`);
	}

	return response.status === 204 ? null : response.json();
}

async function fetchAllRecords() {
	const allRecords = [];
	let page = 1;
	let total = 0;

	do {
		const result = await request(`/records?page=${page}&limit=${PAGE_SIZE}`);
		const pageRecords = Array.isArray(result) ? result : result.data || [];
		allRecords.push(...pageRecords);
		total = Number(result.total ?? allRecords.length);
		page += 1;
	} while (allRecords.length < total);

	return allRecords;
}

function escapeHtml(value = "") {
	return String(value).replace(/[&<>"']/g, (character) => ({
		"&": "&amp;",
		"<": "&lt;",
		">": "&gt;",
		'"': "&quot;",
		"'": "&#39;",
	})[character]);
}

function initials(fullName) {
	return fullName
		.trim()
		.split(/\s+/)
		.slice(0, 2)
		.map((part) => part[0] || "")
		.join("")
		.toLocaleUpperCase("es");
}

function formatDate(value) {
	if (!value) return "Sin fecha";
	return new Intl.DateTimeFormat("es", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
}

function statusClass(status) {
	return `status status--${escapeHtml(status)}`;
}

function renderSummary() {
	const countByStatus = (status) => state.records.filter((record) => record.status === status).length;
	document.querySelector("#total-count").textContent = state.records.length;
	document.querySelector("#active-count").textContent = countByStatus("in_progress");
	document.querySelector("#selected-count").textContent = countByStatus("selected");
	document.querySelector("#received-count").textContent = countByStatus("received");
}

function renderList() {
	const query = elements.search.value.trim().toLocaleLowerCase("es");
	const status = elements.status.value;
	const stage = elements.stage.value;
	state.visibleRecords = state.records.filter((record) => {
		const matchesSearch = !query || `${record.full_name} ${record.email}`.toLocaleLowerCase("es").includes(query);
		return matchesSearch && (!status || record.status === status) && (!stage || record.stage === stage);
	});

	elements.count.textContent = `${state.visibleRecords.length} ${state.visibleRecords.length === 1 ? "candidatura" : "candidaturas"}`;
	elements.empty.hidden = state.visibleRecords.length > 0;
	elements.list.hidden = state.visibleRecords.length === 0;
	elements.tableBody.innerHTML = state.visibleRecords.map((record) => `
		<tr>
			<td>
				<button class="person-link" type="button" data-open-record="${escapeHtml(record.id)}">
					<span class="avatar" aria-hidden="true">${escapeHtml(initials(record.full_name))}</span>
					<span class="person-copy"><strong>${escapeHtml(record.full_name)}</strong><small>${escapeHtml(record.email)}</small></span>
				</button>
			</td>
			<td><span class="position-name">${escapeHtml(record.position)}</span><small class="table-secondary">${Number(record.experience_years) || 0} años de experiencia</small></td>
			<td><span class="${statusClass(record.status)}">${escapeHtml(STATUS_LABELS[record.status] || record.status)}</span></td>
			<td><span class="stage-label">${escapeHtml(STAGE_LABELS[record.stage] || record.stage)}</span></td>
			<td><span class="date-label">${escapeHtml(formatDate(record.applied_at))}</span></td>
			<td class="row-action"><button class="icon-button" type="button" data-open-record="${escapeHtml(record.id)}" aria-label="Ver candidatura de ${escapeHtml(record.full_name)}" title="Ver candidatura"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button></td>
		</tr>`).join("");
}

function setLoading(isLoading) {
	elements.loading.hidden = !isLoading;
	if (isLoading) {
		elements.list.hidden = true;
		elements.empty.hidden = true;
		elements.error.hidden = true;
	}
}

async function loadRecords() {
	setLoading(true);
	try {
		state.records = await fetchAllRecords();
		renderSummary();
		renderList();
		document.querySelector("#updated-at").textContent = `Sincronizado ${new Intl.DateTimeFormat("es", { hour: "2-digit", minute: "2-digit" }).format(new Date())}`;
		elements.error.hidden = true;
	} catch (error) {
		elements.errorMessage.textContent = error.message;
		elements.error.hidden = false;
		elements.list.hidden = true;
	} finally {
		setLoading(false);
	}
}

function showToast(message, isError = false) {
	elements.toast.textContent = message;
	elements.toast.classList.toggle("toast--error", isError);
	elements.toast.hidden = false;
	window.clearTimeout(showToast.timeoutId);
	showToast.timeoutId = window.setTimeout(() => { elements.toast.hidden = true; }, 3600);
}

function openDialog(dialog) {
	if (typeof dialog.showModal === "function") dialog.showModal();
	else dialog.setAttribute("open", "");
}

function closeDialog(dialog) {
	if (typeof dialog.close === "function") dialog.close();
	else dialog.removeAttribute("open");
}

async function openRecord(id) {
	const summary = state.records.find((record) => record.id === id);
	if (!summary) return;
	state.selectedRecord = summary;
	state.selectedNotes = [];
	renderDetail(summary);
	openDialog(elements.detailDialog);

	try {
		const [record, notesResult] = await Promise.all([
			request(`/records/${encodeURIComponent(id)}`),
			request(`/records/${encodeURIComponent(id)}/notes`),
		]);
		state.selectedRecord = record;
		state.selectedNotes = Array.isArray(notesResult) ? notesResult : notesResult.data || notesResult.notes || [];
		renderDetail(record);
	} catch (error) {
		document.querySelector("#detail-error").textContent = `No se pudo cargar el detalle: ${error.message}`;
		document.querySelector("#detail-error").hidden = false;
	}
}

function renderDetail(record) {
	const detailForm = document.querySelector("#edit-candidate-form");
	detailForm.elements.full_name.value = record.full_name || "";
	detailForm.elements.email.value = record.email || "";
	detailForm.elements.phone.value = record.phone || "";
	detailForm.elements.position.value = record.position || "";
	detailForm.elements.linkedin_url.value = record.linkedin_url || "";
	detailForm.elements.cv_url.value = record.cv_url || "";
	detailForm.elements.experience_years.value = record.experience_years ?? 0;
	document.querySelector("#detail-name").textContent = record.full_name;
	document.querySelector("#detail-email").textContent = record.email;
	document.querySelector("#detail-avatar").textContent = initials(record.full_name);
	document.querySelector("#detail-status").value = record.status;
	document.querySelector("#detail-stage").value = record.stage;
	document.querySelector("#detail-applied").textContent = formatDate(record.applied_at);
	document.querySelector("#detail-updated").textContent = formatDate(record.updated_at);
	document.querySelector("#detail-note-count").textContent = record.notes_count ?? state.selectedNotes.length;
	document.querySelector("#linkedin-link").href = safeUrl(record.linkedin_url);
	document.querySelector("#cv-link").href = safeUrl(record.cv_url);
	document.querySelector("#linkedin-link").hidden = !record.linkedin_url;
	document.querySelector("#cv-link").hidden = !record.cv_url;
	document.querySelector("#detail-error").hidden = true;
	renderNotes();
}

function safeUrl(value) {
	try {
		const url = new URL(value);
		return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
	} catch {
		return "#";
	}
}

function renderNotes() {
	const notesElement = document.querySelector("#notes-list");
	document.querySelector("#detail-note-count").textContent = state.selectedNotes.length;
	if (state.selectedNotes.length === 0) {
		notesElement.innerHTML = '<p class="notes-empty">Todavía no hay notas para esta candidatura.</p>';
		return;
	}
	state.selectedNotes.sort((first, second) => new Date(second.created_at) - new Date(first.created_at));
	notesElement.innerHTML = state.selectedNotes.map((note) => `
		<article class="note-item">
			<div class="note-meta"><time datetime="${escapeHtml(note.created_at)}">${escapeHtml(formatDate(note.created_at))}</time><button class="text-button danger-text" type="button" data-delete-note="${escapeHtml(note.id)}">Eliminar</button></div>
			<p>${escapeHtml(note.content)}</p>
		</article>`).join("");
}

function getFormPayload(form) {
	const formData = new FormData(form);
	return {
		full_name: String(formData.get("full_name") || "").trim(),
		email: String(formData.get("email") || "").trim(),
		phone: String(formData.get("phone") || "").trim(),
		position: String(formData.get("position") || "").trim(),
		linkedin_url: String(formData.get("linkedin_url") || "").trim() || null,
		cv_url: String(formData.get("cv_url") || "").trim() || null,
		experience_years: Number(formData.get("experience_years")),
	};
}

function validateCandidate(payload) {
	if (!payload.full_name || !payload.position || !payload.phone) return "Completa nombre, puesto y teléfono.";
	if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(payload.email)) return "Introduce un correo electrónico válido.";
	if (!Number.isFinite(payload.experience_years) || payload.experience_years < 0) return "Los años de experiencia no pueden ser negativos.";
	for (const field of ["linkedin_url", "cv_url"]) {
		if (payload[field] && !/^https?:\/\//i.test(payload[field])) return "Los enlaces deben comenzar con http:// o https://.";
	}
	return "";
}

async function saveCandidate(event) {
	event.preventDefault();
	const form = event.currentTarget;
	const payload = getFormPayload(form);
	const validationMessage = validateCandidate(payload);
	const errorElement = form.querySelector(".form-error");
	if (validationMessage) {
		errorElement.textContent = validationMessage;
		errorElement.hidden = false;
		return;
	}
	errorElement.hidden = true;
	const submitButton = form.querySelector('[type="submit"]');
	submitButton.disabled = true;
	const isEditing = Boolean(form.dataset.recordId);
	try {
		if (isEditing) {
			const record = state.selectedRecord;
			await request(`/records/${encodeURIComponent(record.id)}`, {
				method: "PUT",
				body: JSON.stringify(payload),
			});
			await request(`/records/${encodeURIComponent(record.id)}`, {
				method: "PATCH",
				body: JSON.stringify({ status: record.status, stage: record.stage }),
			});
			showToast("Candidatura actualizada.");
			await loadRecords();
			await openRecord(record.id);
		} else {
			await request("/records", { method: "POST", body: JSON.stringify(payload) });
			closeDialog(elements.candidateDialog);
			form.reset();
			showToast("Candidatura registrada.");
			await loadRecords();
		}
	} catch (error) {
		errorElement.textContent = `No se pudo guardar: ${error.message}`;
		errorElement.hidden = false;
	} finally {
		submitButton.disabled = false;
	}
}

async function updatePipeline() {
	const record = state.selectedRecord;
	const button = document.querySelector("#save-pipeline");
	button.disabled = true;
	try {
		const updated = await request(`/records/${encodeURIComponent(record.id)}`, {
			method: "PATCH",
			body: JSON.stringify({
				status: document.querySelector("#detail-status").value,
				stage: document.querySelector("#detail-stage").value,
			}),
		});
		state.selectedRecord = updated;
		showToast("Estado y etapa actualizados.");
		await loadRecords();
		renderDetail(updated);
	} catch (error) {
		showToast(`No se pudo actualizar el pipeline: ${error.message}`, true);
	} finally {
		button.disabled = false;
	}
}

async function addNote(event) {
	event.preventDefault();
	const form = event.currentTarget;
	const content = String(new FormData(form).get("content") || "").trim();
	if (!content) return;
	const button = form.querySelector('[type="submit"]');
	button.disabled = true;
	try {
		await request(`/records/${encodeURIComponent(state.selectedRecord.id)}/notes`, {
			method: "POST",
			body: JSON.stringify({ content }),
		});
		const notes = await request(`/records/${encodeURIComponent(state.selectedRecord.id)}/notes`);
		state.selectedNotes = Array.isArray(notes) ? notes : notes.data || notes.notes || [];
		form.reset();
		renderNotes();
		showToast("Nota añadida.");
		await loadRecords();
	} catch (error) {
		showToast(`No se pudo añadir la nota: ${error.message}`, true);
	} finally {
		button.disabled = false;
	}
}

async function deleteNote(noteId) {
	if (!window.confirm("¿Eliminar esta nota interna? Esta acción no se puede deshacer.")) return;
	try {
		await request(`/records/${encodeURIComponent(state.selectedRecord.id)}/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" });
		state.selectedNotes = state.selectedNotes.filter((note) => note.id !== noteId);
		renderNotes();
		showToast("Nota eliminada.");
		await loadRecords();
	} catch (error) {
		showToast(`No se pudo eliminar la nota: ${error.message}`, true);
	}
}

function startNewCandidate() {
	const form = document.querySelector("#candidate-form");
	form.reset();
	form.dataset.recordId = "";
	document.querySelector("#candidate-dialog-title").textContent = "Registrar candidatura";
	document.querySelector("#candidate-submit-label").textContent = "Registrar candidatura";
	form.querySelector(".form-error").hidden = true;
	openDialog(elements.candidateDialog);
}

document.querySelector("#retry-load").addEventListener("click", loadRecords);
document.querySelector("#add-candidate").addEventListener("click", startNewCandidate);
document.querySelector("#edit-candidate").addEventListener("click", () => {
	if (!state.selectedRecord) return;
	const form = document.querySelector("#candidate-form");
	form.dataset.recordId = state.selectedRecord.id;
	for (const [key, value] of Object.entries(state.selectedRecord)) {
		if (form.elements[key]) form.elements[key].value = value ?? "";
	}
	document.querySelector("#candidate-dialog-title").textContent = "Editar candidatura";
	document.querySelector("#candidate-submit-label").textContent = "Guardar cambios";
	form.querySelector(".form-error").hidden = true;
	openDialog(elements.candidateDialog);
});
document.querySelector("#save-pipeline").addEventListener("click", updatePipeline);
document.querySelector("#note-form").addEventListener("submit", addNote);
document.querySelector("#candidate-form").addEventListener("submit", saveCandidate);
elements.search.addEventListener("input", () => {
	window.clearTimeout(state.searchTimeout);
	state.searchTimeout = window.setTimeout(renderList, 120);
});
elements.status.addEventListener("change", renderList);
elements.stage.addEventListener("change", renderList);
elements.tableBody.addEventListener("click", (event) => {
	const trigger = event.target.closest("[data-open-record]");
	if (trigger) openRecord(trigger.dataset.openRecord);
});
document.querySelector("#notes-list").addEventListener("click", (event) => {
	const trigger = event.target.closest("[data-delete-note]");
	if (trigger) deleteNote(trigger.dataset.deleteNote);
});
document.querySelectorAll("[data-close-dialog]").forEach((button) => {
	button.addEventListener("click", () => closeDialog(document.querySelector(button.dataset.closeDialog)));
});
document.querySelectorAll("dialog").forEach((dialog) => {
	dialog.addEventListener("click", (event) => {
		if (event.target === dialog) closeDialog(dialog);
	});
});

loadRecords();