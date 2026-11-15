import { requireSession } from "@/lib/server-api";
import ChangePasswordForm from "./change-password-form";

export default async function ChangePasswordPage() {
  await requireSession();
  return <ChangePasswordForm />;
}
