import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { DeploymentStatusBlock } from "@/components/core/flowToolbarComponent/components/deployment-status";
import type { DeploymentStatusResponse } from "@/controllers/API/queries/flows/use-get-deployment-status";
import { type ChangeEvent, useEffect, useState } from "react";
import IconComponent from "../../components/common/genericIconComponent";
import BaseModal from "../baseModal";
import type {
  A2AConfig,
  A2ASkill,
} from "@/controllers/API/queries/flows/use-post-deploy-marketplace";

const INPUT_MODES_OPTIONS = ["text", "text/plain", "image/png", "audio/wav"];
const OUTPUT_MODES_OPTIONS = ["text", "text/plain", "image/png", "audio/wav"];

function defaultSkill(name: string): A2ASkill {
  return {
    id: crypto.randomUUID(),
    name: name || "default",
    description: "",
    tags: [],
  };
}

// ── semver helpers ───────────────────────────────────────────────────────────
type SemVer = { major: number; minor: number; patch: number };

function parseSemver(v: string | undefined): SemVer {
  const [major = 0, minor = 0, patch = 0] = (v ?? "1.0.0")
    .split(".")
    .map((p) => Number.parseInt(p, 10) || 0);
  return { major, minor, patch };
}

function semverString({ major, minor, patch }: SemVer): string {
  return `${major}.${minor}.${patch}`;
}

// Negative if a < b, 0 if equal, positive if a > b.
function compareSemver(a: SemVer, b: SemVer): number {
  if (a.major !== b.major) return a.major - b.major;
  if (a.minor !== b.minor) return a.minor - b.minor;
  return a.patch - b.patch;
}

interface Props {
  open: boolean;
  setOpen: (open: boolean) => void;
  flowName: string;
  onConfirm: (config: A2AConfig) => void;
  isPending: boolean;
  /** Prior deployment's a2a_config — prefills the form on re-publish. */
  initialConfig?: A2AConfig;
  /** Current deployment status — drives the status block + version floor. */
  deploymentStatus?: DeploymentStatusResponse;
}

export default function A2aConfigModal({
  open,
  setOpen,
  flowName,
  onConfirm,
  isPending,
  initialConfig,
  deploymentStatus,
}: Props) {
  const [name, setName] = useState(flowName);
  const [description, setDescription] = useState("");
  const [version, setVersion] = useState<SemVer>({ major: 1, minor: 0, patch: 0 });
  const [streaming, setStreaming] = useState(true);
  const [inputModes, setInputModes] = useState<string[]>(["text"]);
  const [outputModes, setOutputModes] = useState<string[]>(["text"]);
  const [skills, setSkills] = useState<A2ASkill[]>([defaultSkill(flowName)]);

  // The minimum allowed version = whatever is currently deployed (if any).
  const previousVersion: SemVer | null =
    deploymentStatus &&
    deploymentStatus.status !== "not_deployed" &&
    (deploymentStatus.version ??
      deploymentStatus.argocd?.version ??
      deploymentStatus.a2a_config?.version)
      ? parseSemver(
          deploymentStatus.version ??
            deploymentStatus.argocd?.version ??
            deploymentStatus.a2a_config?.version ??
            undefined,
        )
      : null;

  // (Re)seed the form each time the modal opens, prefilling from a prior deploy.
  useEffect(() => {
    if (!open) return;
    const cfg = initialConfig;
    setName(cfg?.name || flowName);
    setDescription(cfg?.description ?? "");
    setStreaming(cfg?.capabilities?.streaming ?? true);
    setInputModes(cfg?.defaultInputModes?.length ? cfg.defaultInputModes : ["text"]);
    setOutputModes(
      cfg?.defaultOutputModes?.length ? cfg.defaultOutputModes : ["text"],
    );
    setSkills(
      cfg?.skills?.length
        ? cfg.skills.map((s) => ({
            id: s.id || crypto.randomUUID(),
            name: s.name || "default",
            description: s.description ?? "",
            tags: s.tags ?? [],
          }))
        : [defaultSkill(cfg?.name || flowName)],
    );
    // Default the version to the current deployed one (user must bump it up).
    setVersion(parseSemver(cfg?.version));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const setVersionField = (field: keyof SemVer, raw: string) => {
    const n = Math.max(0, Number.parseInt(raw, 10) || 0);
    setVersion((prev) => ({ ...prev, [field]: n }));
  };

  const versionTooLow =
    previousVersion !== null && compareSemver(version, previousVersion) < 0;

  const toggleMode = (
    mode: string,
    current: string[],
    setter: (v: string[]) => void,
  ) => {
    if (current.includes(mode)) {
      if (current.length > 1) setter(current.filter((m) => m !== mode));
    } else {
      setter([...current, mode]);
    }
  };

  const updateSkill = (index: number, field: keyof A2ASkill, value: unknown) => {
    setSkills((prev) =>
      prev.map((s, i) => (i === index ? { ...s, [field]: value } : s)),
    );
  };

  const addSkill = () => {
    setSkills((prev) => [...prev, defaultSkill(name)]);
  };

  const removeSkill = (index: number) => {
    if (skills.length > 1) {
      setSkills((prev) => prev.filter((_, i) => i !== index));
    }
  };

  const handleConfirm = () => {
    onConfirm({
      name: name.trim() || flowName,
      description,
      version: semverString(version),
      capabilities: { streaming },
      authentication: { schemes: [] },
      defaultInputModes: inputModes,
      defaultOutputModes: outputModes,
      skills: skills.map(({ id, name: sName, description: sDesc, tags }) => ({
        id,
        name: sName,
        description: sDesc,
        tags,
      })),
    });
  };

  const canSubmit = name.trim().length > 0 && !versionTooLow;

  return (
    <BaseModal open={open} setOpen={setOpen} size="medium" className="pt-4">
      <BaseModal.Header description="Configure how other agents discover and invoke this deployed flow via the A2A protocol.">
        Configure A2A Protocol
      </BaseModal.Header>
      <BaseModal.Content>
        <div className="flex flex-col gap-4 px-1">
          {/* Current deployment status (if already published) */}
          {deploymentStatus && deploymentStatus.status !== "not_deployed" && (
            <div className="rounded-md border bg-muted/40 px-3 py-2">
              <DeploymentStatusBlock data={deploymentStatus} />
            </div>
          )}

          {/* Name */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="a2a-name">Agent Name *</Label>
            <Input
              id="a2a-name"
              value={name}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                setName(e.target.value)
              }
              placeholder="My Agent"
            />
          </div>

          {/* Version — semantic version (major.minor.patch) */}
          <div className="flex flex-col gap-1.5">
            <Label>Version</Label>
            <div className="flex items-center gap-2">
              {(["major", "minor", "patch"] as const).map((field, i) => (
                <div key={field} className="flex items-center gap-2">
                  {i > 0 && <span className="text-muted-foreground">.</span>}
                  <div className="flex flex-col gap-1">
                    <Input
                      type="number"
                      min={0}
                      value={version[field]}
                      onChange={(e: ChangeEvent<HTMLInputElement>) =>
                        setVersionField(field, e.target.value)
                      }
                      className="h-8 w-20 text-center"
                      aria-label={field}
                    />
                    <span className="text-center text-[10px] text-muted-foreground">
                      {field}
                    </span>
                  </div>
                </div>
              ))}
            </div>
            {versionTooLow && previousVersion && (
              <p className="text-xs text-destructive">
                Version must be ≥ current deployed version{" "}
                {semverString(previousVersion)}.
              </p>
            )}
          </div>

          {/* Description */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="a2a-desc">Description</Label>
            <Textarea
              id="a2a-desc"
              value={description}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                setDescription(e.target.value)
              }
              placeholder="What this agent does"
              rows={2}
            />
          </div>

          {/* Streaming toggle */}
          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div>
              <Label className="text-sm font-medium">Streaming</Label>
              <p className="text-xs text-muted-foreground">
                Enable SSE streaming for real-time responses
              </p>
            </div>
            <Switch checked={streaming} onCheckedChange={setStreaming} />
          </div>

          {/* Input Modes */}
          <div className="flex flex-col gap-1.5">
            <Label>Default Input Modes</Label>
            <div className="flex flex-wrap gap-1.5">
              {INPUT_MODES_OPTIONS.map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  size="sm"
                  variant={inputModes.includes(mode) ? "default" : "outline"}
                  onClick={() => toggleMode(mode, inputModes, setInputModes)}
                  className="h-7 text-xs"
                >
                  {mode}
                </Button>
              ))}
            </div>
          </div>

          {/* Output Modes */}
          <div className="flex flex-col gap-1.5">
            <Label>Default Output Modes</Label>
            <div className="flex flex-wrap gap-1.5">
              {OUTPUT_MODES_OPTIONS.map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  size="sm"
                  variant={outputModes.includes(mode) ? "default" : "outline"}
                  onClick={() => toggleMode(mode, outputModes, setOutputModes)}
                  className="h-7 text-xs"
                >
                  {mode}
                </Button>
              ))}
            </div>
          </div>

          {/* Skills */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label>Skills</Label>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={addSkill}
                className="h-7 text-xs"
              >
                <IconComponent name="Plus" className="mr-1 h-3 w-3" />
                Add Skill
              </Button>
            </div>
            {skills.map((skill, i) => (
              <div
                key={skill.id}
                className="flex flex-col gap-2 rounded-md border px-3 py-2"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-muted-foreground">
                    Skill {i + 1}
                  </span>
                  {skills.length > 1 && (
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-6 w-6"
                      onClick={() => removeSkill(i)}
                    >
                      <IconComponent name="X" className="h-3 w-3" />
                    </Button>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="flex flex-col gap-1">
                    <Label className="text-xs">ID *</Label>
                    <Input
                      value={skill.id}
                      onChange={(e: ChangeEvent<HTMLInputElement>) =>
                        updateSkill(i, "id", e.target.value)
                      }
                      className="h-8 text-xs"
                      placeholder="skill-id"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <Label className="text-xs">Name *</Label>
                    <Input
                      value={skill.name}
                      onChange={(e: ChangeEvent<HTMLInputElement>) =>
                        updateSkill(i, "name", e.target.value)
                      }
                      className="h-8 text-xs"
                      placeholder="Skill name"
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">Description</Label>
                  <Input
                    value={skill.description}
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      updateSkill(i, "description", e.target.value)
                    }
                    className="h-8 text-xs"
                    placeholder="What this skill does"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">Tags (comma separated)</Label>
                  <Input
                    value={skill.tags.join(", ")}
                    onChange={(e: ChangeEvent<HTMLInputElement>) =>
                      updateSkill(
                        i,
                        "tags",
                        e.target.value
                          .split(",")
                          .map((t) => t.trim())
                          .filter(Boolean),
                      )
                    }
                    className="h-8 text-xs"
                    placeholder="chat, prompting"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </BaseModal.Content>
      <BaseModal.Footer
        submit={{
          label: isPending ? "Publishing..." : "Publish",
          onClick: handleConfirm,
          disabled: !canSubmit || isPending,
          loading: isPending,
          dataTestId: "btn-confirm-a2a-modal",
        }}
      />
    </BaseModal>
  );
}
