import IncidentSummaryPanel from "@/components/IncidentSummaryPanel";

export default function IncidentSummaryPage() {
  return (
    <main>
      <header className="page-header">
        <div>
          <h1>Incident summary</h1>
          <p>Totals by status, category, origin and clinic.</p>
        </div>
      </header>
      <IncidentSummaryPanel />
    </main>
  );
}
