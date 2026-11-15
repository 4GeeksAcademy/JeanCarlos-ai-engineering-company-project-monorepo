"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { authRequest } from "@/lib/auth";

export default function LoginForm({ passwordChanged }: { passwordChanged: boolean }) {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const response = await authRequest("login", { username, password });
      if (!response.ok) {
        setError(response.status === 401 ? "Credenciales incorrectas." : "No se pudo iniciar sesión.");
        return;
      }
      router.push("/suppliers");
      router.refresh();
    } catch {
      setError("No se pudo conectar con el servicio.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="panel" aria-labelledby="login-title">
        <h1 id="login-title">Iniciar sesión</h1>
        {passwordChanged && <p className="success" role="status">Contraseña actualizada. Inicia sesión de nuevo.</p>}
        <form className="auth-form" onSubmit={submit}>
          <label>Usuario<input autoComplete="username" required value={username} onChange={(e) => setUsername(e.target.value)} /></label>
          <label>Contraseña<input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          {error && <p className="error" role="alert">{error}</p>}
          <button disabled={loading}>{loading ? "Iniciando sesión..." : error ? "Reintentar" : "Entrar"}</button>
        </form>
        <p><Link href="/forgot-password">¿Olvidaste tu contraseña?</Link></p>
      </section>
    </main>
  );
}
