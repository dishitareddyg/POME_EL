interface Props<T extends string> {
  items: { id: T; label: string }[];
  active: T;
  onChange: (id: T) => void;
}

export default function Tabs<T extends string>({ items, active, onChange }: Props<T>) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-slate-200">
      {items.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`-mb-px whitespace-nowrap border-b-2 px-3 py-1.5 text-sm ${
            active === t.id
              ? "border-indigo-600 font-medium text-indigo-700"
              : "border-transparent text-slate-500 hover:text-slate-800"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
