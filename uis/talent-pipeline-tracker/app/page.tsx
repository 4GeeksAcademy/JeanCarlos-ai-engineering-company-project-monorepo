import { Suspense } from "react";
import { CandidateList } from "@/components/candidate-list";
import { NewCandidateButton, PipelineEventsProvider } from "@/components/pipeline-events";
import { PipelineFooter, PipelineHeading, PipelineListFallback, PipelineTopbar } from "@/components/pipeline-shell";

// Topbar and heading (the LCP <h1>) are static and outside the Suspense
// boundary: they are prerendered once and never replaced during hydration.
// Only the part that depends on the URL search params suspends.
export default function Home() {
  return (
    <PipelineEventsProvider>
      <main className="workspace">
        <PipelineTopbar />
        <div className="page-content">
          <PipelineHeading action={<NewCandidateButton />} />
          <Suspense fallback={<PipelineListFallback />}><CandidateList /></Suspense>
          <PipelineFooter />
        </div>
      </main>
    </PipelineEventsProvider>
  );
}
