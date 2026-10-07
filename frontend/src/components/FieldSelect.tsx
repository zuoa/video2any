import { Select } from "@radix-ui/themes";

type SelectOption = { value: string | number; label: string; disabled?: boolean };

interface FieldSelectProps {
  label: string;
  value: string | number;
  options: SelectOption[];
  disabled?: boolean;
  onValueChange: (value: string) => void;
}

export function FieldSelect({ label, value, options, disabled, onValueChange }: FieldSelectProps) {
  return <Select.Root value={String(value)} disabled={disabled} onValueChange={onValueChange}>
    <Select.Trigger className="field-select" aria-label={label} />
    <Select.Content position="popper" variant="soft" className="field-select-menu">
      {options.map(option => <Select.Item key={option.value} value={String(option.value)} disabled={option.disabled}>
        {option.label}
      </Select.Item>)}
    </Select.Content>
  </Select.Root>;
}
