"use client";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
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
