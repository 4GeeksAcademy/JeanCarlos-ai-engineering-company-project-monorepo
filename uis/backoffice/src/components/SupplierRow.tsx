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

  async function save(operation: () => Promise<Supplier>, success: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      onUpdated(await operation());
      setMessage(success);
    } catch (error) {
      setError(error instanceof Error ? error.message : "No se pudo guardar.");
    } finally {
      setBusy(false);
    }
  }

  async function submitRate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = Number(rate);
    if (!Number.isFinite(value) || value <= 0) {
      setError("La tarifa debe ser mayor que cero.");
      return;
    }
    await save(
      async () => await updateSupplierRate(supplier.id, value),
      "Tarifa actualizada.",
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
            aria-label={`Guardar tarifa de ${supplier.name}`}
          >
            Guardar
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
          aria-label={`${supplier.status === "active" ? "Suspender" : "Activar"} ${supplier.name}`}
          onClick={async () =>
            await save(
              async () =>
                await updateSupplierStatus(
                  supplier.id,
                  supplier.status === "active" ? "suspended" : "active",
                ),
              "Estado actualizado.",
            )
          }
        >
          {supplier.status === "active" ? "Suspender" : "Activar"}
        </button>
        {busy && <p role="status">Guardando...</p>}
        {message && <p role="status">{message}</p>}
        {error && <p role="alert" className="error">{error}</p>}
      </td>
    </tr>
  );
}
