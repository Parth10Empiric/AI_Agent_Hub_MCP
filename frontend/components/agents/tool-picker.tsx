"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import {
  OPERATION_HELP,
  OPERATION_LABEL,
  RISK_CLASS,
  RISK_LABEL,
  groupByOperation,
  prettyNamespace,
  prettyToolName,
  safeRisk,
} from "@/lib/tools";

/** The shape both the create wizard and the settings page work with. */
export interface PickableTool {
  name: string;
  namespace: string | null;
  description: string | null;
  operation: string;
  risk_level: string;
  read_only: boolean;
  requires_approval: boolean;
}

/**
 * Choose which tools an agent may use.
 *
 * The rules from Phase 4.5, enforced here rather than trusted:
 *
 *   1. Reads default ON, writes default OFF.
 *   2. Anything requiring approval shows a warning badge.
 *   3. CRITICAL tools need a second confirmation to enable.
 *   4. A live count is always visible.
 *
 * Why this screen earns its complexity: someone is about to give an AI
 * access to their real GitHub account. Being able to see exactly what
 * it can do - and being made to pause on the dangerous ones - is the
 * difference between a demo and software a person will trust.
 */
export function ToolPicker({
  tools,
  selected,
  onChange,
}: {
  tools: PickableTool[];
  /** Set of enabled tool names. A Set, so membership is O(1). */
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  // Which CRITICAL tool is awaiting its second confirmation.
  const [confirming, setConfirming] = useState<PickableTool | null>(null);

  // Grouped once per tools change, not on every keystroke elsewhere in
  // the form. Without useMemo this re-sorts every tool on each render.
  const byNamespace = useMemo(() => {
    const map = new Map<string, PickableTool[]>();

    for (const tool of tools) {
      const key = tool.namespace ?? "other";
      const bucket = map.get(key);

      if (bucket) bucket.push(tool);
      else map.set(key, [tool]);
    }

    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [tools]);

  function toggle(tool: PickableTool, next: boolean) {
    // Rule 3. Turning a critical tool ON asks first; turning one OFF
    // never does - making it harder to become safer would be absurd.
    if (next && safeRisk(tool.risk_level) === "critical") {
      setConfirming(tool);
      return;
    }

    apply(tool.name, next);
  }

  function apply(name: string, next: boolean) {
    // A NEW Set every time. Mutating the existing one and calling
    // onChange with the same reference means React sees no change and
    // skips the re-render - the classic "my checkbox does nothing" bug.
    const copy = new Set(selected);

    if (next) copy.add(name);
    else copy.delete(name);

    onChange(copy);
  }

  function selectAllReads(namespace: string) {
    const copy = new Set(selected);

    for (const tool of tools) {
      if ((tool.namespace ?? "other") === namespace && tool.read_only) {
        copy.add(tool.name);
      }
    }

    onChange(copy);
  }

  return (
    <div className="space-y-8">
      <p
        className="text-sm text-muted-foreground"
        // Announced to a screen reader whenever the number changes, so
        // the running total is not information only sighted users get.
        aria-live="polite"
      >
        <strong className="tabular-nums text-foreground">
          {selected.size}
        </strong>{" "}
        of {tools.length} tools enabled
      </p>

      {byNamespace.map(([namespace, nsTools]) => (
        <section key={namespace} className="space-y-4">
          <div className="flex items-center justify-between border-b pb-2">
            <h3 className="font-semibold">
              {prettyNamespace(namespace)}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                {nsTools.length} tools
              </span>
            </h3>

            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => selectAllReads(namespace)}
            >
              Select all reads
            </Button>
          </div>

          {groupByOperation(nsTools).map(({ operation, items }) => (
            <div key={operation} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide">
                  {OPERATION_LABEL[operation]}
                </span>
                <span className="text-xs text-muted-foreground">
                  {OPERATION_HELP[operation]}
                </span>
              </div>

              <ul className="space-y-1">
                {items.map((tool) => {
                  const risk = safeRisk(tool.risk_level);
                  const checked = selected.has(tool.name);
                  const id = `tool-${tool.name}`;

                  return (
                    <li
                      key={tool.name}
                      className="flex items-start gap-3 rounded-md px-2 py-1.5 hover:bg-muted/50"
                    >
                      <Checkbox
                        id={id}
                        checked={checked}
                        onCheckedChange={(value) =>
                          toggle(tool, value === true)
                        }
                        className="mt-1"
                      />

                      {/* htmlFor makes the whole label a click target
                          and gives the checkbox its accessible name. */}
                      <label htmlFor={id} className="min-w-0 flex-1 cursor-pointer">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">
                            {prettyToolName(tool.name, tool.namespace)}
                          </span>

                          <span
                            className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${RISK_CLASS[risk]}`}
                          >
                            {RISK_LABEL[risk]}
                          </span>

                          {tool.requires_approval && (
                            <span className="text-[11px] text-amber-700 dark:text-amber-400">
                              Asks first
                            </span>
                          )}
                        </span>

                        <span className="block text-sm text-muted-foreground">
                          {tool.description ?? ""}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>

              {operation !== "read" && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
                  These tools change your {prettyNamespace(namespace)}{" "}
                  account. Your agent will ask before using them.
                </p>
              )}
            </div>
          ))}
        </section>
      ))}

      {/* Rule 3: the second confirmation for a CRITICAL tool. */}
      <Dialog
        open={Boolean(confirming)}
        onOpenChange={(open) => !open && setConfirming(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Allow a critical tool?</DialogTitle>
            <DialogDescription>
              {confirming && (
                <>
                  <strong>
                    {prettyToolName(confirming.name, confirming.namespace)}
                  </strong>{" "}
                  can do damage that cannot be undone. Only enable it if
                  you are sure you need it.
                </>
              )}
            </DialogDescription>
          </DialogHeader>

          <DialogFooter>
            {/* Cancel first in the DOM, so it takes focus by default.
                The safe choice should never be the accidental one. */}
            <Button
              type="button"
              variant="ghost"
              onClick={() => setConfirming(null)}
            >
              Cancel
            </Button>

            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                if (confirming) apply(confirming.name, true);
                setConfirming(null);
              }}
            >
              Enable anyway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
