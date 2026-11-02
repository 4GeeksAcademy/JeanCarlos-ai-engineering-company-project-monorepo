import { Suspense } from "react";
import { CandidateDetail } from "@/components/candidate-detail";

interface CandidatePageProps {
  params: Promise<{ id: string }>;
}

async function CandidatePageContent({ params }: CandidatePageProps) {
  const { id } = await params;
  return <CandidateDetail id={id} />;
}

export default function CandidatePage({ params }: CandidatePageProps) {
  return <Suspense fallback={<main className="workspace"><div className="feedback-state" role="status"><span className="spinner" />Cargando candidatura…</div></main>}><CandidatePageContent params={params} /></Suspense>;
}