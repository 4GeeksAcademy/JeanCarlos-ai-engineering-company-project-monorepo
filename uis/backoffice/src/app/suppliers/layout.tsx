import { requireSession } from "@/lib/server-api";

export default async function SuppliersLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  return children;
}
