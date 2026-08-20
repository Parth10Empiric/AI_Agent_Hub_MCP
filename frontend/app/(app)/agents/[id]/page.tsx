import { redirect } from "next/navigation";

/**
 * /agents/{id} has no screen of its own.
 *
 * An agent's "detail view" IS its chat - that is what a user comes for.
 * A server-side redirect sends them straight there without the flash of
 * an empty page a client-side one would cause.
 */
export default async function AgentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  redirect(`/agents/${id}/chat`);
}
