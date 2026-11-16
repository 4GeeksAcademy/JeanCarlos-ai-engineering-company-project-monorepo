import AppNav from "@/components/AppNav";
import { requireSession } from "@/lib/server-api";

export default async function IncidentsLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  return (
    <>
      <AppNav />
      {children}
    </>
  );
}
