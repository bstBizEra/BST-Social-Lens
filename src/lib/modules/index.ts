import type { PlatformModule } from './types';
import { facebookModule } from './facebook';
import { tiktokModule } from './tiktok';

/** Registry — add a module here to support a new platform. */
export const MODULES: readonly PlatformModule[] = [facebookModule, tiktokModule];

export function moduleForResponse(url: string, pageUrl: string): PlatformModule | undefined {
  return MODULES.find((m) => m.matches(url, pageUrl));
}

export function moduleForPage(pageUrl: string): PlatformModule | undefined {
  return MODULES.find((m) => m.matchesPage(pageUrl));
}

export type { PlatformModule, ParseContext } from './types';
