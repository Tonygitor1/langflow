import { useCallback, useEffect, useState } from "react";
import { ProviderVariable } from "@/constants/providerConstants";
import { useGetModelProviders } from "@/controllers/API/queries/models/use-get-model-providers";
import { Provider } from "../components/types";

type ValidationState = "idle" | "validating" | "valid" | "invalid";

interface UseProviderConfigurationOptions {
  selectedProvider: Provider | null;
}

interface UseProviderConfigurationReturn {
  syncedSelectedProvider: Provider | null;
  variableValues: Record<string, string>;
  validationFailed: boolean;
  isSaving: boolean;
  isPending: boolean;
  isDeleting: boolean;
  validationState: ValidationState;
  validationError: string | null;
  providerVariables: ProviderVariable[];
  handleVariableChange: (key: string, value: string) => void;
  handleSaveAllVariables: () => Promise<void>;
  handleDisconnect: () => Promise<void>;
  handleActivateProvider: () => void;
  validateCredentials: () => Promise<boolean>;
  handleModelToggle: (modelName: string, enabled: boolean) => void;
  flushPendingChanges: () => Promise<void>;
  isVariableConfigured: (key: string) => boolean;
  getConfiguredValue: (key: string) => string | null;
  allRequiredFilled: boolean;
  hasNewValuesToSave: boolean;
  requiresConfiguration: boolean;
  canSave: boolean;
  isFetchingAfterSave: boolean;
  isFetchingAfterDisconnect: boolean;
  invalidateProviderQueries: () => void;
}

export const useProviderConfiguration = ({
  selectedProvider,
}: UseProviderConfigurationOptions): UseProviderConfigurationReturn => {
  const [syncedSelectedProvider, setSyncedSelectedProvider] =
    useState<Provider | null>(selectedProvider);

  const { data: modelProviders = [] } = useGetModelProviders(
    {},
    { staleTime: 1000 * 30 },
  );

  // Keep syncedSelectedProvider in sync with prop.
  useEffect(() => {
    setSyncedSelectedProvider(selectedProvider);
  }, [selectedProvider]);

  // Refresh syncedSelectedProvider from latest fetched data.
  useEffect(() => {
    if (syncedSelectedProvider && modelProviders.length > 0) {
      const fresh = modelProviders.find(
        (p) => p.provider === syncedSelectedProvider.provider,
      );
      if (fresh) {
        const modelsChanged =
          JSON.stringify(fresh.models) !==
          JSON.stringify(syncedSelectedProvider.models);
        if (modelsChanged) {
          setSyncedSelectedProvider({ ...syncedSelectedProvider, models: fresh.models || [] });
        }
      }
    }
  }, [modelProviders, syncedSelectedProvider]);

  const noop = useCallback(() => {}, []);
  const noopKV = useCallback((_k: string, _v: string) => {}, []);
  const noopToggle = useCallback((_m: string, _e: boolean) => {}, []);
  const noopAsync = useCallback(async () => {}, []);
  const noopBool = useCallback(async () => true, []);
  const noopNull = useCallback((_k: string) => null as string | null, []);
  const noopFalse = useCallback((_k: string) => false, []);

  return {
    syncedSelectedProvider,
    variableValues: {},
    validationFailed: false,
    isSaving: false,
    isPending: false,
    isDeleting: false,
    validationState: "idle" as ValidationState,
    validationError: null,
    providerVariables: [] as ProviderVariable[],
    handleVariableChange: noopKV,
    handleSaveAllVariables: noopAsync,
    handleDisconnect: noopAsync,
    handleActivateProvider: noop,
    validateCredentials: noopBool,
    handleModelToggle: noopToggle,
    flushPendingChanges: noopAsync,
    isVariableConfigured: noopFalse,
    getConfiguredValue: noopNull,
    allRequiredFilled: false,
    hasNewValuesToSave: false,
    requiresConfiguration: false,
    canSave: false,
    isFetchingAfterSave: false,
    isFetchingAfterDisconnect: false,
    invalidateProviderQueries: noop,
  };
};
