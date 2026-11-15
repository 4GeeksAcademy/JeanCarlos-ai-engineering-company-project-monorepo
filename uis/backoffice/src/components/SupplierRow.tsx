"use client";

import { useState, type FormEvent } from "react";
import {
  updateSupplierRate,
  updateSupplierStatus,
} from "@/lib/suppliers";
import type { Supplier } from "@/types/supplier";

interface Props {
  supplier: Supplier;
  onUpdated: (supplier: Supplier) => void;
}

export default function SupplierRow({ supplier, onUpdated }: Props) {
  const [rate, setRate] = useState(String(supplier.monthly_rate));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [failedAction, setFailedAction] = useState<"rate" | "status" | null>(null);

  async function save(operation: () => Promise<Supplier>, success: string, action: "rate" | "status") {
    setBusy(true);
    setError("");
    setMessage("");
    setFailedAction(null);
    try {
      onUpdated(await operation());
      setMessage(success);
    } catch (error) {
      setError(error instanceof Error ? error.message : "No se pudo guardar.");
      setFailedAction(action);
    } finally {
      setBusy(false);
    }
  }

  async function submitRate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number(rate);
    if (!Number.isFinite(value) || value <= 0) {
      setError("La tarifa debe ser mayor que cero.");
      setFailedAction(null);
      return;
    }
    await save(
      async () => await updateSupplierRate(supplier.id, value),
      "Tarifa actualizada.",
      "rate",
    );
  }

  return (
    <tr>
      <th scope="row">
        {supplier.name}
        <small>{supplier.contact_email}</small>
      </th>
      <td>{supplier.country}</td>
      <td>{supplier.categories.join(", ")}</td>
      <td>
        <p>
          {new Intl.NumberFormat("es", {
            style: "currency",
            currency: supplier.currency,
            maximumFractionDigits: 20,
          }).format(supplier.monthly_rate)}{" "}
          {supplier.currency}
        </p>
        <form onSubmit={submitRate} className="rate-form">
          <label className="sr-only" htmlFor={`rate-${supplier.id}`}>
            Tarifa mensual de {supplier.name}
          </label>
          <input
            id={`rate-${supplier.id}`}
            type="number"
            step="any"
            min="0"
            required
            value={rate}
            disabled={busy}
            onChange={(event) => setRate(event.target.value)}
          />
          <button
            type="submit"
            disabled={busy}
            aria-label={`${failedAction === "rate" ? "Reintentar" : "Guardar"} tarifa de ${supplier.name}`}
          >
            {failedAction === "rate" ? "Reintentar" : "Guardar"}
          </button>
        </form>
        <small>Tarifa actualizada: {supplier.updated_at}</small>
      </td>
      <td>{supplier.compliance_agreement ?? "Sin acuerdo registrado"}</td>
      <td>
        <span className={`badge ${supplier.status}`}>
          {supplier.status === "active" ? "Activo" : "Suspendido"}
        </span>
        <button
          type="button"
          disabled={busy}
          aria-label={`${failedAction === "status" ? "Reintentar cambio de estado de" : supplier.status === "active" ? "Suspender" : "Activar"} ${supplier.name}`}
          onClick={async () =>
            await save(
              async () =>
                await updateSupplierStatus(
                  supplier.id,
                  supplier.status === "active" ? "suspended" : "active",
                ),
              "Estado actualizado.",
              "status",
            )
          }
        >
          {failedAction === "status" ? "Reintentar" : supplier.status === "active" ? "Suspender" : "Activar"}
        </button>
        {busy && <p role="status">Guardando...</p>}
        {message && <p role="status">{message}</p>}
        {error && <p role="alert" className="error">{error}</p>}
      </td>
    </tr>
  );
}
