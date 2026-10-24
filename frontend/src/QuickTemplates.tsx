import "./QuickTemplates.css";

type Kind = "server" | "storage" | "domain" | "hosting";
const templates: { name: string; kind: Kind; hint: string }[] = [
  { name: "Сервер", kind: "server", hint: "VPS или выделенный сервер" },
  { name: "Хранилище", kind: "storage", hint: "Диски и резервные копии" },
  { name: "Домен", kind: "domain", hint: "Сроки и стоимость продления" },
  { name: "Хостинг", kind: "hosting", hint: "Сайты и приложения" },
];

function Illustration({ kind }: { kind: Kind }) {
  return (
    <svg className={`quick-template-art art-${kind}`} viewBox="0 0 240 160" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" focusable="false">
      {kind === "server" && <>
        <g className="rack-row rack-row-top"><rect x="21" y="24" width="198" height="31" rx="3" /><path d="M33 39h2m7 0h2m153 0h10" /></g>
        <g className="rack-row rack-row-middle"><rect x="21" y="57" width="198" height="31" rx="3" /><path d="M33 72h2m7 0h2m153 0h10" /></g>
        <g className="rack-row rack-row-bottom"><rect x="21" y="90" width="198" height="31" rx="3" /><path d="M33 105h2m7 0h2m153 0h10" /></g>
        <path d="M21 123v20m198-20v20" />
      </>}
      {kind === "storage" && <>
        <circle className="storage-circle storage-circle-small" cx="66" cy="111" r="28" vectorEffect="non-scaling-stroke" />
        <circle className="storage-circle storage-circle-large" cx="157" cy="86" r="55" vectorEffect="non-scaling-stroke" />
      </>}
      {kind === "domain" && <>
        <g className="domain-key domain-key-one"><rect x="58" y="29" width="119" height="34" rx="9" /><text x="117" y="52">.online</text></g>
        <g className="domain-key domain-key-two"><rect x="43" y="71" width="67" height="34" rx="9" /><text x="76" y="94">.ru</text></g>
        <g className="domain-key domain-key-three"><rect x="122" y="71" width="67" height="34" rx="9" /><text x="155" y="94">.net</text></g>
        <g className="domain-key domain-key-four"><rect x="58" y="113" width="119" height="34" rx="9" /><text x="117" y="136">.com</text></g>
      </>}
      {kind === "hosting" && <>
        <path className="hosting-w hosting-w-one" d="m36 38 8 28 9-20 9 20 8-28" />
        <path className="hosting-w hosting-w-two" d="m101 70 8 28 9-20 9 20 8-28" />
        <path className="hosting-w hosting-w-three" d="m166 102 8 28 9-20 9 20 8-28" />
      </>}
    </svg>
  );
}

export default function QuickTemplates({ onSelect }: { onSelect: (name: string) => void }) {
  return (
    <section className="quick-templates" aria-labelledby="quick-templates-title">
      <div className="quick-templates-heading"><h2 id="quick-templates-title">Быстро добавить в учёт</h2><p>Стоимость и срок укажите в форме.</p></div>
      <div className="quick-templates-grid">
        {templates.map(({ name, kind, hint }) => (
          <button className="quick-template-card" type="button" key={kind} onClick={() => onSelect(name)} aria-label={`Добавить в учёт: ${name}`}>
            <span className="quick-template-caption">
              <span><span className="quick-template-name">{name}</span><span className="quick-template-hint">{hint}</span></span>
              <span className="quick-template-add" aria-hidden="true">+</span>
            </span>
            <span className="quick-template-visual"><Illustration kind={kind} /></span>
          </button>
        ))}
      </div>
    </section>
  );
}
