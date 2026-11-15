"use client";

import { useEffect, useRef } from "react";
import { track } from "@/services/telemetry";

export function useTelemetryFlow(flow: "inventory_inbound" | "inventory_outbound") {
  const started = useRef<number | null>(null);
  const completed = useRef(false);
  const submitting = useRef(false);
  const step = useRef<"opened" | "product_selected" | "validation_error" | "ready_to_submit">("opened");

  useEffect(() => {
    function abandon() {
      if (started.current === null || completed.current || submitting.current) return;
      const seconds = (performance.now() - started.current) / 1000;
      track("form_flow_abandoned", {
        flow_id: flow, step_id: step.current,
        elapsed_seconds_bucket: seconds <= 10 ? "0_10" : seconds <= 60 ? "11_60" : seconds <= 300 ? "61_300" : "over_300",
      });
      started.current = null;
    }
    window.addEventListener("pagehide", abandon);
    return () => {
      abandon();
      window.removeEventListener("pagehide", abandon);
    };
  }, [flow]);

  return {
    changed() {
      if (started.current === null || completed.current) {
        started.current = performance.now();
        completed.current = false;
        step.current = "opened";
        track("form_flow_started", { flow_id: flow });
      }
    },
    productSelected() { step.current = "product_selected"; },
    invalid() { step.current = "validation_error"; },
    submitting() {
      step.current = "ready_to_submit";
      submitting.current = true;
    },
    settled() { submitting.current = false; },
    completed() { completed.current = true; },
  };
}
