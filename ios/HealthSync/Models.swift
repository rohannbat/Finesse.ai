import Foundation

// Decoded with .convertFromSnakeCase — property names map to the
// backend's snake_case JSON fields.

struct TokenResponse: Decodable {
    let accessToken: String
    let tokenType: String
}

struct AuthorizeURLResponse: Decodable {
    let authorizeUrl: String
}

struct Snapshot: Decodable {
    let id: String
    let date: String

    let sleepDurationMin: Int?
    let sleepEfficiencyPct: Double?
    let deepSleepMin: Int?
    let remSleepMin: Int?

    let hrvMs: Double?
    let restingHr: Int?
    let recoveryScore: Double?

    let strainScore: Double?
    let activeCalories: Int?
    let steps: Int?
    let workoutMinutes: Int?

    let caloriesConsumed: Int?
    let proteinG: Double?
    let carbsG: Double?
    let fatG: Double?

    let sources: [String: Bool]
}

struct Insight: Decodable {
    let id: String
    let date: String
    let insightText: String
    let flags: [String: Bool]
}

struct SyncResponse: Decodable {
    let snapshot: Snapshot
    let insight: Insight?
    let changed: Bool
    let insightError: String?
    let sourceErrors: [String: String]
    /// BMR constant echoed by the backend so energy-balance math can't
    /// drift between client and server.
    let bmrKcal: Int
}

struct FoodEntry: Decodable, Identifiable {
    let id: String
    let date: String
    let name: String
    let calories: Int
    let proteinG: Double?
    let carbsG: Double?
    let fatG: Double?
}

struct FoodLogResponse: Decodable {
    let entries: [FoodEntry]
    let snapshot: Snapshot?
    let insight: Insight?
    let changed: Bool
    let insightError: String?
    let bmrKcal: Int
}

struct FoodListResponse: Decodable {
    let day: String
    let entries: [FoodEntry]
}

struct CoachMessage: Decodable, Identifiable {
    let id: String
    let role: String  // "user" | "assistant"
    let content: String
    let createdAt: String
}

struct CoachChatResponse: Decodable {
    let reply: CoachMessage
}

struct CoachHistoryResponse: Decodable {
    let messages: [CoachMessage]
}
