import { useEffect, useState } from "react";
import type { MarketplaceMetadata } from "@/controllers/API/queries/flows/use-post-deploy-marketplace";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

const CATEGORIES = [
  "productivity",
  "marketing",
  "engineering",
  "research",
  "customer-support",
  "finance",
  "design",
  "operations",
] as const;

const PRICE_UNITS = [
  { value: "month", label: "per month" },
  { value: "task", label: "per task" },
  { value: "one-time", label: "one-time" },
] as const;

interface Props {
  open: boolean;
  flowName: string;
  isPending: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (metadata: MarketplaceMetadata) => void;
}

export default function PublishMarketplaceModal({
  open,
  flowName,
  isPending,
  onOpenChange,
  onSubmit,
}: Props) {
  const [name, setName] = useState(flowName);
  const [producer, setProducer] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] =
    useState<(typeof CATEGORIES)[number]>("productivity");
  const [price, setPrice] = useState("0");
  const [priceUnit, setPriceUnit] =
    useState<(typeof PRICE_UNITS)[number]["value"]>("month");
  const [tags, setTags] = useState("");

  // Re-prefill the agent name each time the modal opens for a (new) flow.
  useEffect(() => {
    if (open) setName(flowName);
  }, [open, flowName]);

  const parsedPrice = Number.parseFloat(price);
  const valid =
    name.trim().length > 0 &&
    producer.trim().length > 0 &&
    description.trim().length > 0 &&
    Number.isFinite(parsedPrice) &&
    parsedPrice >= 0;

  const handleSubmit = () => {
    if (!valid || isPending) return;
    onSubmit({
      name: name.trim(),
      producer: producer.trim(),
      description: description.trim(),
      category,
      price: parsedPrice,
      price_unit: priceUnit,
      tags: tags
        .split(",")
        .map((t) => t.trim().toLowerCase())
        .filter(Boolean),
    });
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !isPending && onOpenChange(o)}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>Publish to Marketplace</DialogTitle>
          <DialogDescription>
            Fill in the listing details shown to marketplace users. The flow is
            deployed as a long-lived agent container once you publish.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mp-name">Agent name</Label>
            <Input
              id="mp-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="DataFlow Orchestrator"
              data-testid="mp-name-input"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mp-producer">Publisher</Label>
            <Input
              id="mp-producer"
              value={producer}
              onChange={(e) => setProducer(e.target.value)}
              placeholder="NeuralWave Labs"
              data-testid="mp-producer-input"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mp-description">Description</Label>
            <Textarea
              id="mp-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this agent do, and who is it for?"
              data-testid="mp-description-input"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Category</Label>
              <Select
                value={category}
                onValueChange={(v) =>
                  setCategory(v as (typeof CATEGORIES)[number])
                }
              >
                <SelectTrigger data-testid="mp-category-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c} value={c} className="capitalize">
                      {c.replace("-", " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Billing</Label>
              <Select
                value={priceUnit}
                onValueChange={(v) =>
                  setPriceUnit(v as (typeof PRICE_UNITS)[number]["value"])
                }
              >
                <SelectTrigger data-testid="mp-price-unit-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRICE_UNITS.map((u) => (
                    <SelectItem key={u.value} value={u.value}>
                      {u.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mp-price">Price (USD, 0 = free)</Label>
            <Input
              id="mp-price"
              type="number"
              min="0"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              data-testid="mp-price-input"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mp-tags">Tags (comma-separated)</Label>
            <Input
              id="mp-tags"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="automation, analytics, llm"
              data-testid="mp-tags-input"
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!valid || isPending}
            data-testid="mp-publish-submit"
          >
            {isPending ? "Publishing..." : "Publish"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
