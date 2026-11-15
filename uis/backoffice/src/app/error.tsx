"use client";

import { useEffect } from "react";
import { canonicalRoute, track } from "@/services/telemetry";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    track("frontend_unhandled_error", {
      route_template: canonicalRoute(),
      error_code: "render_failure",
      release: "telemetry-v1",
    });
  }, []);
  return (
    <main className="auth-page">
      <section className="panel" role="alert">
        <h1>No se pudo cargar la página</h1>
        <p>Comprueba tu conexión e inténtalo de nuevo.</p>
        <button type="button" onClick={reset}>Reintentar</button>
      </section>
    </main>
  );
}
