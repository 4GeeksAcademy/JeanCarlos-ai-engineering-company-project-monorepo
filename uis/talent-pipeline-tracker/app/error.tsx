"use client";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="workspace">
      <section className="feedback-state error-state" role="alert">
        <div><strong>No se pudo cargar la página</strong><p>Comprueba tu conexión e inténtalo de nuevo.</p></div>
        <button className="button button-secondary" type="button" onClick={reset}>Reintentar</button>
      </section>
    </main>
  );
}
