import useFlowsManagerStore from "@/stores/flowsManagerStore";
import useFlowStore from "@/stores/flowStore";
// WatsonX Deploy button hidden to avoid confusion with the marketplace Publish
// button; restore this import + <DeployButton/> below to bring it back.
// import DeployButton from "./deploy-button";
import PublishDropdown from "./deploy-dropdown";
import { FlowDeploymentStatusIndicator } from "./deployment-status";
import MarketplaceDeployButton from "./marketplace-deploy-button";
import PlaygroundButton from "./playground-button";

type FlowToolbarOptionsProps = {
  openApiModal: boolean;
  setOpenApiModal: (open: boolean | ((prev: boolean) => boolean)) => void;
};
const FlowToolbarOptions = ({
  openApiModal,
  setOpenApiModal,
}: FlowToolbarOptionsProps) => {
  const hasIO = useFlowStore((state) => state.hasIO);
  const currentFlowId = useFlowsManagerStore((s) => s.currentFlow?.id);

  return (
    <div className="flex items-center gap-1">
      <PlaygroundButton hasIO={hasIO} />
      <PublishDropdown
        openApiModal={openApiModal}
        setOpenApiModal={setOpenApiModal}
      />
      <MarketplaceDeployButton />
      {/* <DeployButton /> */}
      <FlowDeploymentStatusIndicator flowId={currentFlowId} />
    </div>
  );
};

export default FlowToolbarOptions;
