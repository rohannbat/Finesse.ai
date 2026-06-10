import Foundation

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private struct ErrorBody: Decodable {
    let detail: String?
}

final class APIClient {
    static let shared = APIClient()

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    var baseURL: URL {
        let raw = UserDefaults.standard.string(forKey: "serverURL") ?? "http://localhost:8000"
        return URL(string: raw) ?? URL(string: "http://localhost:8000")!
    }

    var token: String? {
        get {
            let value = UserDefaults.standard.string(forKey: "authToken") ?? ""
            return value.isEmpty ? nil : value
        }
        set { UserDefaults.standard.set(newValue ?? "", forKey: "authToken") }
    }

    // MARK: - Endpoints

    func register(email: String, password: String) async throws {
        let response: TokenResponse = try await request(
            "/api/auth/register", method: "POST",
            body: ["email": email, "password": password]
        )
        token = response.accessToken
    }

    func login(email: String, password: String) async throws {
        let response: TokenResponse = try await request(
            "/api/auth/login", method: "POST",
            body: ["email": email, "password": password]
        )
        token = response.accessToken
    }

    /// platform: "whoop" or "oura"
    func authorizeURL(platform: String) async throws -> URL {
        let response: AuthorizeURLResponse = try await request("/api/integrations/\(platform)/connect")
        guard let url = URL(string: response.authorizeUrl) else {
            throw APIError(message: "Backend returned an invalid authorize URL")
        }
        return url
    }

    /// One round trip: syncs all connected platforms, regenerates the insight
    /// server-side only if the data changed. Called by the dashboard's 30s
    /// polling loop.
    func syncNow() async throws -> SyncResponse {
        try await request("/api/sync", method: "POST")
    }

    /// Replace semantics: sets the day's nutrition totals outright (the
    /// backend merges into today's snapshot and regenerates the insight if
    /// values changed).
    func logNutrition(calories: Int, proteinG: Double, carbsG: Double?, fatG: Double?) async throws -> SyncResponse {
        var body: [String: Any] = ["calories": calories, "protein_g": proteinG]
        if let carbsG { body["carbs_g"] = carbsG }
        if let fatG { body["fat_g"] = fatG }
        return try await request("/api/nutrition", method: "POST", body: body)
    }

    func logout() {
        token = nil
    }

    // MARK: - Plumbing

    private func request<T: Decodable>(
        _ path: String,
        method: String = "GET",
        body: [String: Any]? = nil
    ) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 25  // under the 30s poll interval
        if let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError(message: "No HTTP response from server")
        }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 401 {
                logout()  // expired/invalid token — drop back to login
            }
            let detail = (try? decoder.decode(ErrorBody.self, from: data))?.detail
            throw APIError(message: detail ?? "Server error (HTTP \(http.statusCode))")
        }
        return try decoder.decode(T.self, from: data)
    }
}
