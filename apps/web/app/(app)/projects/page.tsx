"use client";

import { useApp } from "@/components/features/app-shell";
import { ProjectManagementWorkspace } from "@/components/features/project-management";

export default function ProjectsPage() {
  const { appMode } = useApp();
  return <ProjectManagementWorkspace active={appMode === "normal"} />;
}
