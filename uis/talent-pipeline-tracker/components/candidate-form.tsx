"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import type { Candidate } from "@/types/tracker";

const CandidateFormDialog = dynamic(
  () => import("@/components/candidate-form-dialog").then((module) => module.CandidateFormDialog),
  {
    ssr: false,
    loading: () => <p role="status">Cargando formulario…</p>,
  },
);

interface CandidateFormProps {
  candidate?: Candidate;
  onSaved: (candidate: Candidate) => void | Promise<void>;
}

export function CandidateForm({ candidate, onSaved }: CandidateFormProps) {
  const [open, setOpen] = useState(false);
  const isEditing = Boolean(candidate);

  return <>
    <button
      className={isEditing ? "button button-secondary" : "button button-primary"}
      type="button"
      onClick={() => setOpen(true)}
    >
      {isEditing ? "Editar datos" : <><span aria-hidden="true">＋</span> Nueva candidatura</>}
    </button>
    {open && (
      <CandidateFormDialog
        key={candidate?.id ?? "new"}
        candidate={candidate}
        onSaved={onSaved}
        onClose={() => setOpen(false)}
      />
    )}
  </>;
}
