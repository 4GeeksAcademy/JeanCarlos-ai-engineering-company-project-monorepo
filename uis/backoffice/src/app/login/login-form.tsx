"use client";

import { AuthenticationForm } from "@healthcore/auth/components";
import { captureAuthentication, track } from "@/services/telemetry";

function captureLogin(response: Response | null) {
  if (response) void captureAuthentication("login", response);
  else track("auth_login_failed", { failure_reason: "service_unavailable" });
}

export default function LoginForm({ passwordChanged }: { passwordChanged: boolean }) {
  return (
    <AuthenticationForm
      mode="login" homePath="/suppliers" passwordChanged={passwordChanged} passwordRecovery
      onLoginResponse={captureLogin}
    />
  );
}
