// Placeholder for pages whose build is queued next (Connectors, Agents).
// Real routes so the sidebar links work; replaced page-by-page as each
// wireframe is built out.
export default function ComingSoon({ title, note }: { title: string; note: string }) {
  return (
    <main className="mx-auto max-w-7xl px-6 py-16 text-center">
      <h1 className="text-2xl font-extrabold text-ink">{title}</h1>
      <p className="mt-2 text-inksoft">{note}</p>
    </main>
  );
}
