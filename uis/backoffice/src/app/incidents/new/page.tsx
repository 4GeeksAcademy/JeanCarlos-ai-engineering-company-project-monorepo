import IncidentForm from "@/components/IncidentForm";

export default function NewIncidentPage() {
  return (
    <main>
      <header className="page-header">
        <div>
          <h1>Report an incident</h1>
          <p>All fields are required. The clinic is required for every origin.</p>
        </div>
      </header>
      <IncidentForm />
    </main>
  );
}
