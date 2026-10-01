import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import { useGetDeploymentStatus } from "@/controllers/API/queries/flows/use-get-deployment-status";
import {
  usePostDeployMarketplace,
  type A2AConfig,
  type DeployMarketplaceResponse,
} from "@/controllers/API/queries/flows/use-post-deploy-marketplace";
import useAlertStore from "@/stores/alertStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import A2aConfigModal from "@/modals/a2aConfigModal";

export default function MarketplaceDeployButton() {
  const currentFlow = useFlowsManagerStore((s) => s.currentFlow);
  const flowId = currentFlow?.id;
  // Mirrors is_app_flow() in api/v1/agent_apps.py; read the live canvas, not the saved flow.
  const isAppFlow = useFlowStore((s) =>
    s.nodes.some((n) => n.data?.type === "AgentAppSkeleton"),
  );
  const [showA2aModal, setShowA2aModal] = useState(false);
  const setSuccessData = useAlertStore((s) => s.setSuccessData);
  const setNoticeData = useAlertStore((s) => s.setNoticeData);
  const setErrorData = useAlertStore((s) => s.setErrorData);
  const queryClient = useQueryClient();

  // Prior deployment (if any) — powers form prefill + version floor + status.
  const { data: deploymentStatus } = useGetDeploymentStatus({ flowId });

  const { mutate: deploy, isPending } = usePostDeployMarketplace({
    // Non-blocking: the executor now returns immediately with status
    // "starting". We just confirm the request landed, close the modal, and let
    // the header status indicator poll readiness in the background.
    onSuccess: (result?: DeployMarketplaceResponse) => {
      setShowA2aModal(false);
      setSuccessData({
        title: result?.unchanged ? "No changes: the published app is already up to date" : "Deployment started",
      });
      for (const warning of result?.translation_warnings ?? []) {
        setNoticeData({ title: warning });
      }
      if (flowId) {
        queryClient.invalidateQueries({
          queryKey: ["useGetDeploymentStatus", flowId],
        });
      }
    },
    onError: (err: any) => {
      const detail =
        err?.response?.data?.detail ?? err?.message ?? "Deploy failed";
      setErrorData({ title: "Publish failed", list: [String(detail)] });
    },
  });

  const handleClick = () => {
    if (!flowId || isPending) return;
    setShowA2aModal(true);
  };

  const handleA2aConfirm = (a2aConfig: A2AConfig) => {
    if (!flowId) return;
    deploy({ flowId, a2a_config: a2aConfig });
  };

  const [openingStudio, setOpeningStudio] = useState(false);
  const openStudio = async () => {
    if (!flowId) return;
    const { nodes, setNode } = useFlowStore.getState();
    const skeletonNode = nodes.find((n) => n.data?.type === "AgentAppSkeleton");
    const queryNode = nodes.find((n) => n.data?.type === "AgentAppQuery");
    const template = skeletonNode?.data?.node?.template;
    if (!skeletonNode || !template) return;
    if (!template.draft_id) {
      setErrorData({
        title: "Update the Agent App Skeleton node first",
        list: ["This node is from before Studio existed. Use its 'Update' badge, then try again."],
      });
      return;
    }
    setOpeningStudio(true);
    try {
      const { data } = await api.post<{ draft_id: string; url: string }>(
        `${getURL("FLOWS")}/${flowId}/studio-draft`,
        {
          skeleton: template.skeleton?.value ?? null,
          service: queryNode?.data?.node?.template?.service?.value ?? null,
          draft_id: template.draft_id.value || null,
        },
      );
      if (template.draft_id.value !== data.draft_id) {
        setNode(skeletonNode.id, (old) => {
          const node = old.data.node as { template: Record<string, any> };
          return {
            ...old,
            data: {
              ...old.data,
              node: {
                ...node,
                template: { ...node.template, draft_id: { ...node.template.draft_id, value: data.draft_id } },
              },
            },
          } as unknown as typeof old;
        });
        setNoticeData({ title: "The screens now come from the Studio draft. Save the flow to keep the link." });
      }
      window.open(data.url, "_blank", "noopener");
    } catch (err: any) {
      setErrorData({
        title: "Could not open Studio",
        list: [String(err?.response?.data?.detail ?? err?.message ?? err)],
      });
    } finally {
      setOpeningStudio(false);
    }
  };

  return (
    <>
      {isAppFlow && (
        <button
          type="button"
          onClick={openStudio}
          disabled={openingStudio || !flowId}
          className="relative mr-1 inline-flex h-8 items-center justify-start gap-1.5 rounded border border-border bg-background px-2 text-sm font-normal hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="edit-app-ui-btn"
          title="Edit the app's screens in the marketplace Studio"
        >
          <ForwardedIconComponent name="LayoutDashboard" className="h-4 w-4" />
          <span className="font-normal text-mmd">Edit app UI</span>
        </button>
      )}
      <button
        type="button"
        onClick={handleClick}
        disabled={isPending || !flowId}
        className="relative inline-flex h-8 items-center justify-start gap-1.5 rounded border border-primary bg-background px-2 text-sm font-normal text-primary hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-50"
        data-testid="marketplace-deploy-btn"
        title="Publish to Marketplace"
      >
        <ForwardedIconComponent
          name="Store"
          className={`h-4 w-4 ${isPending ? "animate-pulse" : ""}`}
        />
        <span className="font-normal text-mmd">
          {isPending ? "Publishing..." : "Publish"}
        </span>
      </button>
      <A2aConfigModal
        open={showA2aModal}
        setOpen={setShowA2aModal}
        flowName={currentFlow?.name ?? ""}
        onConfirm={handleA2aConfirm}
        isPending={isPending}
        initialConfig={deploymentStatus?.a2a_config}
        deploymentStatus={deploymentStatus}
        appMode={isAppFlow}
      />
    </>
  );
}
