"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext, useContext, useEffect, useState,
  type FormEvent, type InputHTMLAttributes, type ReactNode,
} from "react";
import {
  AuthError, SESSION_EVENT, TOKEN_KEY, clearSession, getAccount, getToken, login,
  passwordError, responseJson, saveProfile, type AuthResponseListener,
} from "../lib/client";
import type { Account } from "../types";

const AccountContext = createContext<{
  account: Account;
  updateAccount: (account: Account) => void;
} | null>(null);

export function SessionBoundary({ children, homePath }: { children: ReactNode; homePath: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = ["/login", "/register", "/forgot-password", "/reset-password"].includes(pathname);
  const [session, setSession] = useState<{ path: string; account?: Account; error?: string }>({ path: "" });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function verify() {
      try {
        const account = await getAccount(controller.signal);
        if (!controller.signal.aborted) setSession({ path: pathname, account });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof AuthError && error.status === 401) {
          router.replace("/login");
        } else {
          setSession({ path: pathname, error: error instanceof Error ? error.message : "No se pudo verificar la sesión." });
        }
      }
    }
    function sessionChanged(event: Event) {
      if (event instanceof StorageEvent && event.key !== TOKEN_KEY && event.key !== null) return;
      try {
        if (!getToken()) {
          setSession({ path: pathname });
          if (!isPublic) router.replace("/login");
        } else if (!isPublic) {
          setSession({ path: pathname });
          void verify();
        }
      } catch (error) {
        setSession({ path: pathname, error: error instanceof Error ? error.message : "No se pudo leer la sesión." });
      }
    }
    window.addEventListener(SESSION_EVENT, sessionChanged);
    window.addEventListener("storage", sessionChanged);
    if (!isPublic) void verify();
    return () => {
      controller.abort();
      window.removeEventListener(SESSION_EVENT, sessionChanged);
      window.removeEventListener("storage", sessionChanged);
    };
  }, [pathname, isPublic, retry, router]);

  if (isPublic) return children;
  if (session.path !== pathname || !session.account) {
    return (
      <main className="hc-auth-page">
        {session.path === pathname && session.error ? (
          <section className="hc-auth-panel">
            <p role="alert">{session.error}</p>
            <button type="button" onClick={() => {
              setSession({ path: pathname });
              setRetry((value) => value + 1);
            }}>Reintentar</button>
            <p><Link href="/login">Ir a iniciar sesión</Link></p>
          </section>
        ) : <p role="status">Comprobando sesión...</p>}
      </main>
    );
  }

  function logout() {
    try {
      clearSession();
      router.replace("/login");
    } catch (error) {
      setSession({ path: pathname, error: error instanceof Error ? error.message : "No se pudo cerrar la sesión." });
    }
  }

  return (
    <AccountContext.Provider value={{
      account: session.account,
      updateAccount: (account) => setSession({ path: pathname, account }),
    }}>
      <nav className="hc-session-bar" aria-label="Cuenta y sesión">
        <Link href={homePath}>Inicio</Link>
        <Link href="/account/profile">Mi perfil</Link>
        <button type="button" onClick={logout}>Cerrar sesión</button>
      </nav>
      {children}
    </AccountContext.Provider>
  );
}

function Field({
  label, error, name, ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; name: string; error?: string }) {
  const id = `hc-${name}`;
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <input {...props} id={id} name={name} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} />
      {error && <p className="hc-auth-error" id={`${id}-error`}>{error}</p>}
    </div>
  );
}

export function AuthenticationForm({
  mode, homePath, passwordChanged = false, passwordRecovery = false, onLoginResponse,
}: {
  mode: "login" | "register"; homePath: string; passwordChanged?: boolean; passwordRecovery?: boolean;
  /** Optional hook (e.g. telemetry) notified of every login attempt. */
  onLoginResponse?: AuthResponseListener;
}) {
  const router = useRouter();
  const registering = mode === "register";
  const [busy, setBusy] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    setError("");
    setFields({});
    if (registering) {
      const validation = passwordError(password, String(form.get("confirmation") ?? ""));
      if (validation) {
        setFields({ password: validation });
        return;
      }
    }
    setBusy(true);
    let accountCreated = false;
    try {
      if (registering) {
        await responseJson(await fetch("/api/account/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email, password,
            name: String(form.get("name") ?? "").trim() || null,
            phone: String(form.get("phone") ?? "").trim() || null,
            address: String(form.get("address") ?? "").trim() || null,
          }),
          cache: "no-store",
        }));
        accountCreated = true;
        setRegistered(true);
      }
      await login(email, password, onLoginResponse);
      router.replace(homePath);
    } catch (error) {
      if (error instanceof AuthError) setFields(error.fields);
      const detail = error instanceof Error ? error.message : "No se pudo conectar con el servicio.";
      setError(accountCreated ? `Cuenta creada, pero no se pudo iniciar sesión. ${detail}` : detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="hc-auth-page">
      <section className="hc-auth-panel" aria-labelledby="hc-auth-title">
        <h1 id="hc-auth-title">{registering ? "Crear cuenta" : "Iniciar sesión"}</h1>
        {passwordChanged && <p role="status">Contraseña actualizada. Inicia sesión de nuevo.</p>}
        {registered && <p role="status">Cuenta creada correctamente.</p>}
        {error && <p className="hc-auth-error" role="alert">{error}</p>}
        {!registered && <form onSubmit={submit}>
          <fieldset disabled={busy}>
            <Field label="Correo electrónico" name="email" type="email" autoComplete="email" required error={fields.email} />
            <Field label="Contraseña" name="password" type="password" autoComplete={registering ? "new-password" : "current-password"} minLength={registering ? 8 : undefined} required error={fields.password} />
            {registering && <>
              <Field label="Confirmar contraseña" name="confirmation" type="password" autoComplete="new-password" minLength={8} required />
              <Field label="Nombre (opcional)" name="name" autoComplete="name" maxLength={200} error={fields.name} />
              <Field label="Teléfono (opcional)" name="phone" type="tel" autoComplete="tel" maxLength={50} error={fields.phone} />
              <Field label="Dirección (opcional)" name="address" autoComplete="street-address" maxLength={500} error={fields.address} />
              <p>Las nuevas cuentas se registran con el rol de usuario.</p>
            </>}
            <button disabled={busy}>{busy ? "Procesando..." : registering ? "Registrarme" : "Entrar"}</button>
          </fieldset>
        </form>}
        <p><Link href={registering ? "/login" : "/register"}>{registering ? "Ir a iniciar sesión" : "Crear una cuenta"}</Link></p>
        {!registering && passwordRecovery && <p><Link href="/forgot-password">¿Olvidaste tu contraseña?</Link></p>}
      </section>
    </main>
  );
}

export function ProfilePage() {
  const session = useContext(AccountContext);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  if (!session) throw new Error("El perfil requiere una sesión autenticada.");
  const { account, updateAccount } = session;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setFields({});
    setMessage("");
    try {
      const profile = await saveProfile({
        name: String(form.get("name") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        address: String(form.get("address") ?? "").trim() || null,
      });
      updateAccount({ ...account, profile });
      setMessage("Perfil actualizado correctamente.");
    } catch (error) {
      if (error instanceof AuthError) setFields(error.fields);
      setError(error instanceof Error ? error.message : "No se pudo actualizar el perfil.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="hc-auth-page">
      <section className="hc-auth-panel" aria-labelledby="hc-profile-title">
        <h1 id="hc-profile-title">Mi perfil</h1>
        <Field label="Correo electrónico" name="account-email" type="email" readOnly value={account.email ?? ""} />
        {!account.email && <p>Esta cuenta no tiene un correo asignado. Contacta con un administrador.</p>}
        <form onSubmit={submit}>
          <fieldset disabled={busy}>
            <Field label="Nombre" name="name" autoComplete="name" maxLength={200} defaultValue={account.profile?.name ?? ""} error={fields.name} />
            <Field label="Teléfono" name="phone" type="tel" autoComplete="tel" maxLength={50} defaultValue={account.profile?.phone ?? ""} error={fields.phone} />
            <Field label="Dirección" name="address" autoComplete="street-address" maxLength={500} defaultValue={account.profile?.address ?? ""} error={fields.address} />
            <button disabled={busy}>{busy ? "Guardando..." : "Guardar perfil"}</button>
          </fieldset>
        </form>
        {error && <p className="hc-auth-error" role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
      </section>
    </main>
  );
}
