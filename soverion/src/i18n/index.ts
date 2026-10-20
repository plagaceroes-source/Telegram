import { ru } from "./ru";
import type { Locale } from "./config";

const dictionaries = { ru };
export const getDictionary = (l: Locale) => dictionaries[l];
