import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/utils/utils";
import { type ChangeEvent, useState } from "react";
import IconComponent from "../../components/common/genericIconComponent";
import BaseModal from "../baseModal";
import type { A2AConfig, A2ASkill } from "@/controllers/API/queries/flows/use-post-deploy-marketplace";

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

interface Props {
  open: boolean;
  setOpen: (open: boolean) => void;
  flowName: string;
  onConfirm: (config: A2AConfig) => void;
  isPending: boolean;
}

export default function A2aConfigModal({
  open,
  setOpen,
  flowName,
  onConfirm,
  isPending,
}: Props) {
  const [name, setName] = useState(flowName);
  const [description, setDescription] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [streaming, setStreaming] = useState(true);
  const [inputModes, setInputModes] = useState<string[]>(["text"]);
  const [outputModes, setOutputModes] = useState<string[]>(["text"]);
  const [skills, setSkills] = useState<A2ASkill[]>([defaultSkill(flowName)]);

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
      version,
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

  const canSubmit = name.trim().length > 0;

  return (
    <BaseModal open={open} setOpen={setOpen} size="medium" className="pt-4">
      <BaseModal.Header description="Configure how other agents discover and invoke this deployed flow via the A2A protocol.">
        Configure A2A Protocol
      </BaseModal.Header>
      <BaseModal.Content>
        <div className="flex flex-col gap-4 px-1">
          {/* Name + Version */}
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2 flex flex-col gap-1.5">
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
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="a2a-version">Version</Label>
              <Input
                id="a2a-version"
                value={version}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setVersion(e.target.value)
                }
                placeholder="1.0.0"
              />
            </div>
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
          label: isPending ? "Deploying..." : "Publish",
          onClick: handleConfirm,
          disabled: !canSubmit || isPending,
          loading: isPending,
          dataTestId: "btn-confirm-a2a-modal",
        }}
      />
    </BaseModal>
  );
}
