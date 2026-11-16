"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { authRequest, passwordError } from "@/lib/auth";
import { clearSession } from "@healthcore/auth/client";

export default function ChangePasswordForm() {
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validation = passwordError(password, confirmation);
    if (validation) {
      setError(validation);
      return;
    }
    setError("");
    setLoading(true);
    try {
      const response = await authRequest("change-password", {
        current_password: current, new_password: password,
      });
      if (response.status === 401) {
        router.replace("/login");
        return;
      }
      if (!response.ok) {
        setError(response.status === 400 ? "La contraseña actual es incorrecta." : "No se pudo cambiar la contraseña.");
        return;
      }
      clearSession();
      router.replace("/login?password_changed=1");
    } catch {
      setError("No se pudo conectar con el servicio.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="panel" aria-labelledby="change-title">
        <h1 id="change-title">Cambiar contraseña</h1>
        <form className="auth-form" onSubmit={submit}>
          <label>Contraseña actual<input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
          <label>Nueva contraseña<input type="password" autoComplete="new-password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          <label>Confirmar contraseña<input type="password" autoComplete="new-password" minLength={8} required value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></label>
          {error && <p className="error" role="alert">{error}</p>}
          <button disabled={loading}>{loading ? "Guardando..." : error ? "Reintentar" : "Cambiar contraseña"}</button>
        </form>
        <p><Link href="/suppliers">Volver a proveedores</Link></p>
      </section>
    </main>
  );
}
