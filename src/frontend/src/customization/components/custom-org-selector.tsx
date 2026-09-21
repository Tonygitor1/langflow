import { Building2, Check, ChevronsUpDown, User } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/utils/utils";

const PERSONAL_LABEL = "Personal";

interface Profiles {
  /** "" = personal. */
  active: string;
  available: string[];
}

/**
 * Read the profiles the backend recorded at login.
 *
 * Display only: switching re-runs the OIDC flow, which re-derives the allowed
 * profiles from a fresh Keycloak token, so editing this cookie gains nothing.
 */
function readProfiles(): Profiles | null {
  const raw = document.cookie
    .split("; ")
    .find((c) => c.startsWith("sso_profiles="))
    ?.split("=")
    .slice(1)
    .join("=");
  if (!raw) return null;
  try {
    // Base64 padding makes the value "special", so the server quotes it.
    const unquoted = decodeURIComponent(raw).replace(/^"|"$/g, "");
    const parsed = JSON.parse(atob(unquoted));
    if (!Array.isArray(parsed?.available)) return null;
    return parsed as Profiles;
  } catch {
    return null;
  }
}

export function CustomOrgSelector() {
  const profiles = readProfiles();
  // Nothing to switch between: stay out of the header entirely.
  if (!profiles || profiles.available.length < 2) return <></>;

  const label = (orgId: string) => (orgId === "" ? PERSONAL_LABEL : orgId);

  const switchTo = (orgId: string) => {
    if (orgId === profiles.active) return;
    const query = orgId ? `?org_id=${encodeURIComponent(orgId)}` : "";
    window.location.href = `/api/v1/login/oidc/authorize${query}`;
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="group flex items-center gap-1.5 rounded-md px-2 py-1 text-sm hover:bg-muted"
        data-testid="org_selector_button"
      >
        {profiles.active ? (
          <Building2 className="h-[14px] w-[14px] text-muted-foreground" />
        ) : (
          <User className="h-[14px] w-[14px] text-muted-foreground" />
        )}
        <span className="max-w-[10rem] truncate">{label(profiles.active)}</span>
        <ChevronsUpDown className="h-[14px] w-[14px] text-muted-foreground group-hover:text-primary" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[14rem]">
        {profiles.available.map((orgId) => (
          <DropdownMenuItem
            key={orgId || "personal"}
            className="cursor-pointer justify-between"
            onClick={() => switchTo(orgId)}
            data-testid={`org_selector_item_${orgId || "personal"}`}
          >
            <span className="flex items-center gap-2">
              {orgId ? (
                <Building2 className="h-[14px] w-[14px]" />
              ) : (
                <User className="h-[14px] w-[14px]" />
              )}
              <span className="truncate">{label(orgId)}</span>
            </span>
            <Check
              className={cn(
                "h-[14px] w-[14px]",
                orgId === profiles.active ? "opacity-100" : "opacity-0",
              )}
            />
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
