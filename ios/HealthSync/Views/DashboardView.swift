import SwiftUI

@MainActor
final class DashboardViewModel: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var insight: Insight?
    @Published var lastSynced: Date?
    @Published var lastChanged: Date?
    @Published var errorMessage: String?
    @Published var isSyncing = false
    @Published var autoSync = true
    @Published var bmrKcal = 1700  // overwritten by the backend's value on first response

    static let pollInterval: Duration = .seconds(30)

    func sync() async {
        guard !isSyncing else { return }
        isSyncing = true
        defer { isSyncing = false }
        do {
            apply(try await APIClient.shared.syncNow())
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Shared by the sync poll and the nutrition-log sheet — both return
    /// the same response shape.
    func apply(_ response: SyncResponse) {
        snapshot = response.snapshot
        if let fresh = response.insight {
            insight = fresh
        }
        lastSynced = Date()
        if response.changed {
            lastChanged = Date()
        }
        bmrKcal = response.bmrKcal
        var problems: [String] = []
        if let insightError = response.insightError {
            problems.append("insight: \(insightError)")
        }
        problems.append(contentsOf: response.sourceErrors.map { "\($0.key): \($0.value)" })
        errorMessage = problems.isEmpty ? nil : problems.joined(separator: "\n")
    }

    /// kcal relative to (active calories + BMR); nil until nutrition is logged.
    var energyBalance: Int? {
        guard let consumed = snapshot?.caloriesConsumed else { return nil }
        return consumed - ((snapshot?.activeCalories ?? 0) + bmrKcal)
    }
}

struct DashboardView: View {
    @AppStorage("authToken") private var authToken: String = ""
    @StateObject private var model = DashboardViewModel()
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @State private var showLogSheet = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    insightCard
                    metricsGrid
                    logFoodButton
                    syncStatus
                }
                .padding()
            }
            .sheet(isPresented: $showLogSheet) {
                NutritionLogSheet(snapshot: model.snapshot) { response in
                    model.apply(response)
                }
            }
            .navigationTitle("Today")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Toggle("Auto-sync (30s)", isOn: $model.autoSync)
                        Button("Sync now") { Task { await model.sync() } }
                        Button("Connect WHOOP") { connect(platform: "whoop") }
                        Button("Connect Oura") { connect(platform: "oura") }
                        Divider()
                        Button("Log out", role: .destructive) {
                            APIClient.shared.logout()
                            authToken = ""
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .refreshable { await model.sync() }
            // Polling loop: restarts whenever the toggle flips; pauses in background.
            .task(id: model.autoSync) {
                guard model.autoSync else { return }
                while !Task.isCancelled {
                    await model.sync()
                    try? await Task.sleep(for: DashboardViewModel.pollInterval)
                }
            }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { Task { await model.sync() } }
            }
        }
    }

    // MARK: - Sections

    private var insightCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Today's Insight", systemImage: "sparkles")
                .font(.headline)
            if let insight = model.insight {
                Text(insight.insightText)
                    .font(.body)
                flagChips(insight.flags)
            } else {
                Text("No insight yet — connect WHOOP from the ⋯ menu and wait for the first sync.")
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
    }

    @ViewBuilder
    private func flagChips(_ flags: [String: Bool]) -> some View {
        let active = flags.filter(\.value).keys.sorted()
        if !active.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack {
                    ForEach(active, id: \.self) { flag in
                        Text(flag.replacingOccurrences(of: "_", with: " "))
                            .font(.caption.weight(.medium))
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(.orange.opacity(0.2), in: Capsule())
                    }
                }
            }
        }
    }

    private var metricsGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            metric("Sleep", model.snapshot?.sleepDurationMin.map { formatMinutes($0) }, "bed.double.fill")
            metric("Sleep efficiency", model.snapshot?.sleepEfficiencyPct.map { "\(Int($0))%" }, "moon.zzz.fill")
            metric("HRV", model.snapshot?.hrvMs.map { String(format: "%.0f ms", $0) }, "waveform.path.ecg")
            metric("Resting HR", model.snapshot?.restingHr.map { "\($0) bpm" }, "heart.fill")
            metric("Recovery", model.snapshot?.recoveryScore.map { "\(Int($0))/100" }, "battery.75percent")
            metric("Strain", model.snapshot?.strainScore.map { String(format: "%.1f", $0) }, "bolt.fill")
            metric("Steps", model.snapshot?.steps.map { $0.formatted() }, "figure.walk")
            metric("Active kcal", model.snapshot?.activeCalories.map { "\($0)" }, "flame.fill")
            metric("Workout", model.snapshot?.workoutMinutes.map { formatMinutes($0) }, "dumbbell.fill")
            metric("Calories in", model.snapshot?.caloriesConsumed.map { "\($0)" }, "fork.knife")
            metric("Energy balance", model.energyBalance.map { formatBalance($0) }, "scalemass.fill")
            metric("Protein", model.snapshot?.proteinG.map { "\(Int($0)) g" }, "p.circle.fill")
        }
    }

    private var logFoodButton: some View {
        Button {
            showLogSheet = true
        } label: {
            Label("Log food", systemImage: "fork.knife.circle.fill")
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
        }
        .buttonStyle(.borderedProminent)
    }

    private func metric(_ title: String, _ value: String?, _ icon: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: icon)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value ?? "—")
                .font(.title3.weight(.semibold))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.gray.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }

    private var syncStatus: some View {
        VStack(spacing: 4) {
            if model.isSyncing {
                Label("Syncing…", systemImage: "arrow.triangle.2.circlepath")
            } else if let synced = model.lastSynced {
                Text("Last sync \(synced.formatted(date: .omitted, time: .standard))")
            }
            if let changed = model.lastChanged {
                Text("Data last changed \(changed.formatted(date: .omitted, time: .standard))")
            }
            if let error = model.errorMessage {
                Text(error)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    // MARK: - Actions

    private func connect(platform: String) {
        Task {
            do {
                let url = try await APIClient.shared.authorizeURL(platform: platform)
                openURL(url)  // approve in Safari; backend callback stores the tokens
            } catch {
                model.errorMessage = error.localizedDescription
            }
        }
    }

    private func formatMinutes(_ minutes: Int) -> String {
        minutes >= 60 ? "\(minutes / 60)h \(minutes % 60)m" : "\(minutes)m"
    }

    private func formatBalance(_ kcal: Int) -> String {
        kcal >= 0 ? "+\(kcal) kcal" : "−\(abs(kcal)) kcal"
    }
}

#Preview {
    DashboardView()
}
