import InventoryMovementForm from "@/components/InventoryMovementForm";

export default async function InboundPage({
  searchParams,
}: {
  searchParams: Promise<{ supply_id?: string | string[] }>;
}) {
  const { supply_id } = await searchParams;
  return <InventoryMovementForm direction="inbound" initialSupplyId={typeof supply_id === "string" ? supply_id : ""} />;
}
