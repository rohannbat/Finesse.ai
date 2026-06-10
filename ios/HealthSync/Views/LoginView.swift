import SwiftUI

struct LoginView: View {
    @AppStorage("serverURL") private var serverURL: String = "http://localhost:8000"
    @AppStorage("authToken") private var authToken: String = ""

    @State private var email = ""
    @State private var password = ""
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("http://localhost:8000", text: $serverURL)
                        .keyboardType(.URL)
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                }
                Section("Account") {
                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                    SecureField("Password (8+ characters)", text: $password)
                }
                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red)
                    }
                }
                Section {
                    Button("Log in") { authenticate { try await APIClient.shared.login(email: email, password: password) } }
                        .disabled(isWorking || email.isEmpty || password.isEmpty)
                    Button("Create account") { authenticate { try await APIClient.shared.register(email: email, password: password) } }
                        .disabled(isWorking || email.isEmpty || password.isEmpty)
                }
            }
            .navigationTitle("HealthSync")
            .overlay { if isWorking { ProgressView() } }
        }
    }

    private func authenticate(_ action: @escaping () async throws -> Void) {
        isWorking = true
        errorMessage = nil
        Task {
            defer { isWorking = false }
            do {
                try await action()
                // APIClient stored the token; @AppStorage flips the root view.
                authToken = APIClient.shared.token ?? ""
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}

#Preview {
    LoginView()
}
