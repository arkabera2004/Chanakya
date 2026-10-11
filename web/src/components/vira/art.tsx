/**
 * Spot illustrations of Vira for empty states and quiet corners of the desk.
 *
 * The pictures were generated from the Vira reference still (docs/brand/flow-stills,
 * prompts in docs/brand/GOOGLE_FLOW_PROMPTS.md) and are drawn on her own near-black
 * ground. They sit in a dark rounded tile in both themes, the same way the app mark does,
 * so no rectangle shows against a light page.
 */

type SpotName = "leads" | "passes" | "sources" | "costs" | "products" | "documents" | "profile";

export function Spot({ name, size = 132, className = "" }: { name: SpotName; size?: number; className?: string }) {
  return (
    // Decorative: the heading and sentence beside it carry the meaning.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`/brand/art/${name}.webp`}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      className={`shrink-0 rounded-2xl border border-muted bg-[#131213] ${className}`}
      style={{ width: size, height: size }}
    />
  );
}

export const EmptyLeads = ({ width }: { width?: number }) => <Spot name="leads" size={width} />;
export const EmptyPasses = ({ width }: { width?: number }) => <Spot name="passes" size={width} />;
export const EmptySources = ({ width }: { width?: number }) => <Spot name="sources" size={width} />;
export const EmptyCosts = ({ width }: { width?: number }) => <Spot name="costs" size={width} />;
export const EmptyProducts = ({ width }: { width?: number }) => <Spot name="products" size={width} />;
export const EmptyDocuments = ({ width }: { width?: number }) => <Spot name="documents" size={width} />;
