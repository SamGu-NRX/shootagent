import { createHttpListingApi, type ListingApi } from "../api/listing";
import { createMockListingApi } from "../api/mock/listingMock";
import { useStore, type ApiMode } from "./store";

// One instance per mode: the mock keeps its leads in memory, so a booking on the buyer page shows up in the
// realtor view in the same tab.
const apis: Partial<Record<ApiMode, ListingApi>> = {};

export function listingApiFor(mode: ApiMode): ListingApi {
  return (apis[mode] ??= mode === "mock" ? createMockListingApi() : createHttpListingApi());
}

export function useListingApi(): ListingApi {
  return listingApiFor(useStore((s) => s.apiMode));
}
