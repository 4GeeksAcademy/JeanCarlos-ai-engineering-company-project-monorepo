import { Suspense } from "react";
import { CandidateList } from "@/components/candidate-list";

function ListFallback() {
  return <main className="workspace"><div className="feedback-state" role="status"><span className="spinner" />Cargando el pipeline…</div></main>;
}

export default function Home() {
  return <Suspense fallback={<ListFallback />}><CandidateList /></Suspense>;
}