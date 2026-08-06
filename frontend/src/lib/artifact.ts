// Artifacts-lite parsing: a ```document fenced block becomes a side-panel doc.
// Lives outside the component file so Fast Refresh sees pure components only.

export interface Artifact {
  title: string
  content: string
}

const DOC_BLOCK = /```document\n(.*?)```/s

export function extractArtifact(content: string): Artifact | null {
  const match = DOC_BLOCK.exec(content)
  if (!match?.[1]) return null
  const lines = match[1].trim().split('\n')
  return { title: lines[0]?.trim() || 'Document', content: lines.slice(1).join('\n').trim() }
}

export function stripArtifact(content: string): string {
  return content.replace(DOC_BLOCK, '').trim()
}
