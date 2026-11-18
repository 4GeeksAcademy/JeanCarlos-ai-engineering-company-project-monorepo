"use client";

import { createContext, useCallback, useContext, useEffect, useRef, type ReactNode } from "react";
import { CandidateForm } from "@/components/candidate-form";

/*
 * The heading (with the "Nueva candidatura" button) is rendered outside the
 * Suspense boundary so its <h1> — the LCP element — is server-rendered once and
 * never replaced during hydration (performance audit). This context lets the
 * button, which lives in the heading, tell the list to reload after a save.
 */
type Listener = () => void | Promise<void>;
const PipelineEvents = createContext<{ subscribe: (listener: Listener) => () => void; emit: () => Promise<void> } | null>(null);

export function PipelineEventsProvider({ children }: { children: ReactNode }) {
  const listeners = useRef(new Set<Listener>());
  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => { listeners.current.delete(listener); };
  }, []);
  const emit = useCallback(async () => {
    await Promise.all([...listeners.current].map((listener) => listener()));
  }, []);
  return <PipelineEvents.Provider value={{ subscribe, emit }}>{children}</PipelineEvents.Provider>;
}

/** Runs `onSaved` whenever a candidature is created from the heading button. */
export function useCandidateSaved(onSaved: Listener) {
  const events = useContext(PipelineEvents);
  const latest = useRef(onSaved);
  useEffect(() => { latest.current = onSaved; }, [onSaved]);
  useEffect(() => events?.subscribe(() => latest.current()), [events]);
}

export function NewCandidateButton() {
  const events = useContext(PipelineEvents);
  return <CandidateForm onSaved={async () => { await events?.emit(); }} />;
}
