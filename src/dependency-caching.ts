import * as os from "os";
import { join } from "path";

import * as actionsCache from "@actions/cache";
import * as glob from "@actions/glob";

import { getTotalCacheSize } from "./caching-utils";
import { Config } from "./config-utils";
import { Language } from "./languages";
import { Logger } from "./logging";
import { getRequiredEnvParam } from "./util";

/**
 * Caching configuration for a particular language.
 */
interface CacheConfig {
  /** The paths of directories on the runner that should be included in the cache. */
  paths: string[];
  /**
   * Patterns for the paths of files whose contents affect which dependencies are used
   * by a project. We find all files which match these patterns, calculate a hash for
   * their contents, and use that hash as part of the cache key.
   */
  hash: string[];
}

const CODEQL_DEPENDENCY_CACHE_PREFIX = "codeql-dependencies";
const CODEQL_DEPENDENCY_CACHE_VERSION = 1;

/**
 * Default caching configurations per language.
 */
const CODEQL_DEFAULT_CACHE_CONFIG: { [language: string]: CacheConfig } = {
  java: {
    paths: [
      // Maven
      join(os.homedir(), ".m2", "repository"),
      // Gradle
      join(os.homedir(), ".gradle", "caches"),
    ],
    hash: [
      // Maven
      "**/pom.xml",
      // Gradle
      "**/*.gradle*",
      "**/gradle-wrapper.properties",
      "buildSrc/**/Versions.kt",
      "buildSrc/**/Dependencies.kt",
      "gradle/*.versions.toml",
      "**/versions.properties",
    ],
  },
  csharp: {
    paths: [join(os.homedir(), ".nuget", "packages")],
    hash: [
      // NuGet
      "**/packages.lock.json",
    ],
  },
  go: {
    paths: [join(os.homedir(), "go", "pkg", "mod")],
    hash: ["**/go.sum"],
  },
};

async function makeGlobber(patterns: string[]): Promise<glob.Globber> {
  return glob.create(patterns.join("\n"));
}

/**
 * Attempts to restore dependency caches for the languages being analyzed.
 *
 * @param languages The languages being analyzed.
 * @param logger A logger to record some informational messages to.
 * @returns A list of languages for which dependency caches were restored.
 */
export async function downloadDependencyCaches(
  languages: Language[],
  logger: Logger,
): Promise<Language[]> {
  const restoredCaches: Language[] = [];

  for (const language of languages) {
    const cacheConfig = CODEQL_DEFAULT_CACHE_CONFIG[language];

    if (cacheConfig === undefined) {
      logger.info(
        `Skipping download of dependency cache for ${language} as we have no caching configuration for it.`,
      );
      continue;
    }

    // Check that we can find files to calculate the hash for the cache key from, so we don't end up
    // with an empty string.
    const globber = await makeGlobber(cacheConfig.hash);

    if ((await globber.glob()).length === 0) {
      logger.info(
        `Skipping download of dependency cache for ${language} as we cannot calculate a hash for the cache key.`,
      );
      continue;
    }

    const primaryKey = await cacheKey(language, cacheConfig);
    const restoreKeys: string[] = [await cachePrefix(language)];

    logger.info(
      `Downloading cache for ${language} with key ${primaryKey} and restore keys ${restoreKeys.join(
        ", ",
      )}`,
    );

    const hitKey = await actionsCache.restoreCache(
      cacheConfig.paths,
      primaryKey,
      restoreKeys,
    );

    if (hitKey !== undefined) {
      logger.info(`Cache hit on key ${hitKey} for ${language}.`);
      restoredCaches.push(language);
    }
  }

  return restoredCaches;
}

/**
 * Attempts to store caches for the languages that were analyzed.
 *
 * @param config The configuration for this workflow.
 * @param logger A logger to record some informational messages to.
 */
export async function uploadDependencyCaches(config: Config, logger: Logger) {
  for (const language of config.languages) {
    const cacheConfig = CODEQL_DEFAULT_CACHE_CONFIG[language];

    if (cacheConfig === undefined) {
      logger.info(
        `Skipping upload of dependency cache for ${language} as we have no caching configuration for it.`,
      );
      continue;
    }

    // Check that we can find files to calculate the hash for the cache key from, so we don't end up
    // with an empty string.
    const globber = await makeGlobber(cacheConfig.hash);

    if ((await globber.glob()).length === 0) {
      logger.info(
        `Skipping upload of dependency cache for ${language} as we cannot calculate a hash for the cache key.`,
      );
      continue;
    }

    const size = await getTotalCacheSize(cacheConfig.paths, logger);
    const key = await cacheKey(language, cacheConfig);

    logger.info(
      `Uploading cache of size ${size} for ${language} with key ${key}`,
    );

    await actionsCache.saveCache(cacheConfig.paths, key);
  }
}

/**
 * Computes a cache key for the specified language.
 *
 * @param language The language being analyzed.
 * @param cacheConfig The cache configuration for the language.
 * @returns A cache key capturing information about the project(s) being analyzed in the specified language.
 */
async function cacheKey(
  language: Language,
  cacheConfig: CacheConfig,
): Promise<string> {
  const hash = await glob.hashFiles(cacheConfig.hash.join("\n"));
  return `${await cachePrefix(language)}${hash}`;
}

/**
 * Constructs a prefix for the cache key, comprised of a CodeQL-specific prefix, a version number that
 * can be changed to invalidate old caches, the runner's operating system, and the specified language name.
 *
 * @param language The language being analyzed.
 * @returns The prefix that identifies what a cache is for.
 */
async function cachePrefix(language: Language): Promise<string> {
  const runnerOs = getRequiredEnvParam("RUNNER_OS");
  return `${CODEQL_DEPENDENCY_CACHE_PREFIX}-${CODEQL_DEPENDENCY_CACHE_VERSION}-${runnerOs}-${language}-`;
}