
import React, { useState, useRef, useEffect } from "react";
import { ExtendedHoursConfig, type SlotMarket } from "../../store/chartWorkstationStore";

interface ExtendedHoursToggleProps {
  value: ExtendedHoursConfig;
  onChange: (config: Partial<ExtendedHoursConfig>) => void;
  market: SlotMarket;
  disabled?: boolean;
}

const ExtendedHoursToggle: React.FC<ExtendedHoursToggleProps> = ({
  value,
  onChange,
  market,
  disabled,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative inline-block" ref={dropdownRef}>
      <button
        className={`px-2 py-0.5 text-xs font-bold rounded border ${
          value.enabled
            ? "bg-terminal-accent/10 border-terminal-accent text-terminal-accent"
            : "bg-terminal-bg border-terminal-border text-terminal-muted"
        } ${disabled ? "opacity-50 cursor-not-allowed" : "hover:brightness-110"}`}
        onClick={() => !disabled && onChange({ enabled: !value.enabled })}
        onContextMenu={(e) => {
            e.preventDefault();
            setIsOpen(!isOpen);
        }}
        title="Extended Trading Hours (Right-click for options)"
        disabled={disabled}
      >
        ETH
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-1 w-64 bg-terminal-panel border border-terminal-border rounded shadow-xl z-50 p-3 text-xs text-terminal-text">
          <div className="font-bold mb-2 border-b border-terminal-border pb-1">ETH Settings</div>

          <div className="space-y-2">
            <label className="flex items-center space-x-2 cursor-pointer">
              <input
                type="checkbox"
                checked={value.showPreMarket}
                onChange={(e) => onChange({ showPreMarket: e.target.checked })}
                className="rounded border-terminal-border bg-terminal-bg text-terminal-accent focus:ring-terminal-accent"
              />
              <span>{market === "US" ? "Pre-Market (4AM–9:30AM)" : "Pre-Open (9:00–9:15)"}</span>
            </label>

            <label className="flex items-center space-x-2 cursor-pointer">
              <input
                type="checkbox"
                checked={value.showAfterHours}
                onChange={(e) => onChange({ showAfterHours: e.target.checked })}
                className="rounded border-terminal-border bg-terminal-bg text-terminal-accent focus:ring-terminal-accent"
              />
              <span>{market === "US" ? "After-Hours (4PM–8PM)" : "Closing (3:30–3:40)"}</span>
            </label>

            <hr className="border-terminal-border" />

            <div className="space-y-1">
              <span className="text-terminal-muted block mb-1">Display Mode:</span>
              <select
                value={value.visualMode}
                onChange={(e) => onChange({ visualMode: e.target.value as any })}
                className="w-full bg-terminal-bg border border-terminal-border rounded px-1 py-0.5 text-xs focus:border-terminal-accent outline-none"
              >
                <option value="merged">Merged (continuous timeline)</option>
                <option value="separated">Separated (session gaps)</option>
                <option value="overlay">Overlay (ETH as background)</option>
              </select>
            </div>

            <div className="space-y-1">
              <span className="text-terminal-muted block mb-1">Color Scheme:</span>
              <select
                value={value.colorScheme}
                onChange={(e) => onChange({ colorScheme: e.target.value as any })}
                className="w-full bg-terminal-bg border border-terminal-border rounded px-1 py-0.5 text-xs focus:border-terminal-accent outline-none"
              >
                <option value="dimmed">Dimmed ETH bars</option>
                <option value="distinct">Distinct session colors</option>
                <option value="same">Same as RTH</option>
              </select>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ExtendedHoursToggle;
