"use client";

import { useCallback, useEffect, useState } from "react";
import { useInventoryError } from "@/lib/inventory";

export function useInventoryResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  fallback: string,
) {
  const [data, setData] = useState<T | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const errorMessage = useInventoryError();

  useEffect(() => {
    const controller = new AbortController();
    async function fetchData() {
      try {
        const result = await load(controller.signal);
        if (!controller.signal.aborted) {
          setData(result);
          setState("ready");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(errorMessage(error, fallback));
          setState("error");
        }
      }
    }
    void fetchData();
    return () => controller.abort();
  }, [load, fallback, reload, errorMessage]);

  const retry = useCallback(() => {
    setError("");
    setState("loading");
    setReload((value) => value + 1);
  }, []);

  return { data, state, error, retry };
}
