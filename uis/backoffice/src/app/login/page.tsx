import LoginForm from "./login-form";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ password_reset?: string; password_changed?: string }>;
}) {
  const params = await searchParams;
  return <LoginForm passwordChanged={params.password_reset === "1" || params.password_changed === "1"} />;
}
