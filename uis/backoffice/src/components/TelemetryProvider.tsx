"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { trackNavigation } from "@/services/telemetry";

export default function TelemetryProvider() {
  const pathname = usePathname();
  useEffect(() => {
    void trackNavigation(pathname);
  }, [pathname]);
  return null;
}
