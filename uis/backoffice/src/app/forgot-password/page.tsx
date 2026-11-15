"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { authRequest } from "@/lib/auth";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const response = await authRequest("forgot-password", { email });
      if (!response.ok) {
        setError("No se pudo procesar la solicitud. Inténtalo más tarde.");
        return;
      }
      setSent(true);
    } catch {
      setError("No se pudo conectar con el servicio.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="panel" aria-labelledby="forgot-title">
        <h1 id="forgot-title">Recuperar contraseña</h1>
        {sent ? (
          <p className="success" role="status">Si existe una cuenta con ese correo, recibirás instrucciones.</p>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <label>Correo electrónico<input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            {error && <p className="error" role="alert">{error}</p>}
            <button disabled={loading}>{loading ? "Enviando..." : error ? "Reintentar" : "Enviar instrucciones"}</button>
          </form>
        )}
        <p><Link href="/login">Volver a iniciar sesión</Link></p>
      </section>
    </main>
  );
}
