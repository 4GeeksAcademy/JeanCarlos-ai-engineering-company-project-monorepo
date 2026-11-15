"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { authRequest, passwordError } from "@/lib/auth";

export default function ResetPasswordForm({ token }: { token: string }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    window.history.replaceState(null, "", "/reset-password");
  }, []);

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
      const response = await authRequest("reset-password", { token, new_password: password });
      if (!response.ok) {
        setError(response.status === 400 ? "El enlace es inválido, ha caducado o ya se usó." : "No se pudo cambiar la contraseña.");
        return;
      }
      router.replace("/login?password_reset=1");
    } catch {
      setError("No se pudo conectar con el servicio.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="panel" aria-labelledby="reset-title">
        <h1 id="reset-title">Restablecer contraseña</h1>
        {!token ? (
          <p className="error" role="alert">Falta el enlace de recuperación. <Link href="/forgot-password">Solicitar otro</Link>.</p>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <label>Nueva contraseña<input type="password" autoComplete="new-password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
            <label>Confirmar contraseña<input type="password" autoComplete="new-password" minLength={8} required value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></label>
            {error && <p className="error" role="alert">{error}</p>}
            <button disabled={loading}>{loading ? "Guardando..." : "Cambiar contraseña"}</button>
          </form>
        )}
      </section>
    </main>
  );
}
