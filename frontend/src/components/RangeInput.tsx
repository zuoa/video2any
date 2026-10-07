import * as Slider from "@radix-ui/react-slider";

interface RangeInputProps {
  label: string;
  min: number;
  max: number;
  step?: number;
  value: number;
  disabled?: boolean;
  onValueChange: (value: number) => void;
}

export function RangeInput({ label, min, max, step = 1, value, disabled, onValueChange }: RangeInputProps) {
  return <Slider.Root className="range-input" min={min} max={max} step={step}
    value={[value]} disabled={disabled || max <= min} onValueChange={values => onValueChange(values[0])}>
    <Slider.Track className="range-track"><Slider.Range className="range-fill" /></Slider.Track>
    <Slider.Thumb className="range-thumb" aria-label={label} />
  </Slider.Root>;
}
