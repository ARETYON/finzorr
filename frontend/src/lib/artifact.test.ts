import { describe, expect, it } from 'vitest'
import { extractArtifact, stripArtifact } from './artifact'

describe('extractArtifact', () => {
  it('parses a titled document block', () => {
    const message = 'Here you go:\n```document\nMy Report\nline one\nline two\n```\nDone.'
    const artifact = extractArtifact(message)
    expect(artifact).toEqual({ title: 'My Report', content: 'line one\nline two' })
  })

  it('returns null when there is no block', () => {
    expect(extractArtifact('just a normal answer')).toBeNull()
  })

  it('returns null for an empty block', () => {
    expect(extractArtifact('```document\n```')).toBeNull()
  })

  it('treats the first non-empty line as the title', () => {
    const artifact = extractArtifact('```document\n\nbody only\n```')
    expect(artifact).toEqual({ title: 'body only', content: '' })
  })
})

describe('stripArtifact', () => {
  it('removes the block and keeps commentary', () => {
    const message = 'Intro.\n```document\nT\nbody\n```\nOutro.'
    const stripped = stripArtifact(message)
    expect(stripped).not.toContain('body')
    expect(stripped).toContain('Intro.')
    expect(stripped).toContain('Outro.')
  })
})
