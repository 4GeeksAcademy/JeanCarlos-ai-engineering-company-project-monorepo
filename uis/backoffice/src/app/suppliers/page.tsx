"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import SupplierForm from "@/components/SupplierForm";
import SupplierRow from "@/components/SupplierRow";
import { createSupplier, listSuppliers } from "@/lib/suppliers";
import {
  CATEGORIES,
  type Supplier,
  type SupplierCreate,
  type SupplierFilters,
} from "@/types/supplier";

export default function SuppliersPage() {
  const [filters, setFilters] = useState<SupplierFilters>({
    country: "",
    category: "",
  });
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "fulfilled" | "rejected">("loading");
  const [loadError, setLoadError] = useState("");
  const [createError, setCreateError] = useState("");
  const [message, setMessage] = useState("");
  const [creating, setCreating] = useState(false);
  const [formKey, setFormKey] = useState(0);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      let state: "fulfilled" | "rejected" = "rejected";
      try {
        const result = await listSuppliers(filters, controller.signal);
        if (!controller.signal.aborted) {
          setSuppliers(result);
          setLoadError("");
          state = "fulfilled";
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setLoadError(
            error instanceof Error ? error.message : "No se pudo cargar el directorio.",
          );
        }
      } finally {
        if (!controller.signal.aborted) setLoadState(state);
      }
    }
    void load();
    return () => controller.abort();
  }, [filters, reload]);

  function changeFilters(next: SupplierFilters) {
    setLoadState("loading");
    setLoadError("");
    setFilters(next);
  }

  async function submitSupplier(payload: SupplierCreate) {
    setCreating(true);
    setCreateError("");
    setMessage("");
    try {
      const supplier = await createSupplier(payload);
      setMessage(`Proveedor ${supplier.name} registrado.`);
      setFormKey((key) => key + 1);
      setLoadState("loading");
      setReload((value) => value + 1);
    } catch (error) {
      setCreateError(
        `${error instanceof Error ? error.message : "No se pudo crear el proveedor."} Comprueba el directorio antes de volver a enviar el alta.`,
      );
    } finally {
      setCreating(false);
    }
  }

  function updated(supplier: Supplier) {
    setSuppliers((current) =>
      current.map((item) => (item.id === supplier.id ? supplier : item)),
    );
  }

  return (
    <main>
      <header>
        <p>HealthCore Digital</p>
        <h1>Directorio de proveedores</h1>
        <p>Gestion de servicios, tarifas mensuales y acuerdos de cumplimiento.</p>
        <p><Link href="/account/change-password">Cambiar contraseña</Link> · <Link href="/inventory/products">Inventario</Link></p>
      </header>
      <section className="panel" aria-labelledby="directory-title">
        <h2 id="directory-title">Proveedores</h2>
        <div className="filters">
          <label>
            Pais
            <select
              value={filters.country}
              onChange={(event) => {
                const country = event.target.value;
                if (country === "" || country === "USA" || country === "UK") {
                  changeFilters({ ...filters, country });
                }
              }}
            >
              <option value="">Todos</option>
              <option value="USA">USA</option>
              <option value="UK">UK</option>
            </select>
          </label>
          <label>
            Categoria
            <select
              value={filters.category}
              onChange={(event) => {
                const category = CATEGORIES.find(
                  (item) => item === event.target.value,
                );
                changeFilters({ ...filters, category: category ?? "" });
              }}
            >
              <option value="">Todas</option>
              {CATEGORIES.map((category) => (
                <option key={category} value={category}>{category}</option>
              ))}
            </select>
          </label>
        </div>
        {loadState === "loading" && <p role="status">Cargando proveedores...</p>}
        {loadState === "rejected" && (
          <div role="alert" className="error">
            <p>{loadError}</p>
            <button type="button" onClick={() => {
              setLoadState("loading");
              setLoadError("");
              setReload((value) => value + 1);
            }}>
              Reintentar
            </button>
            <p><Link href="/login">Iniciar sesión</Link></p>
          </div>
        )}
        {loadState === "fulfilled" && (
          <>
            <p role="status">{suppliers.length} proveedores encontrados.</p>
            <div className="table-scroll" role="region" aria-label="Tabla de proveedores" tabIndex={0}>
              <table>
                <caption className="sr-only">Directorio de proveedores de HealthCore</caption>
                <thead>
                  <tr>
                    <th scope="col">Nombre</th>
                    <th scope="col">Pais</th>
                    <th scope="col">Categorias</th>
                    <th scope="col">Tarifa mensual</th>
                    <th scope="col">Cumplimiento</th>
                    <th scope="col">Estado y acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {suppliers.map((supplier) => (
                    <SupplierRow key={supplier.id} supplier={supplier} onUpdated={updated} />
                  ))}
                  {suppliers.length === 0 && (
                    <tr><td colSpan={6}>No hay proveedores con estos filtros.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
      {message && <p role="status" className="success">{message}</p>}
      {createError && <p role="alert" className="error">{createError}</p>}
      <SupplierForm key={formKey} busy={creating} onSubmit={submitSupplier} />
    </main>
  );
}
