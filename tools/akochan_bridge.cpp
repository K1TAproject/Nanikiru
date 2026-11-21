// Transport only: upstream akochan owns every strategy decision.
#include "ai_src/selector.hpp"
int main(int argc, char** argv) try {
    if (argc != 2) return 2;
    omp_set_num_threads(NPROCS);
    set_tactics_one(load_json_from_file(argv[1]));
    std::string line, error;
    while (std::getline(std::cin, line)) {
        auto input = json11::Json::parse(line, error);
        if (!error.empty()) return 3;
        Moves record(input["record"].array_items());
        auto moves = ai(record, input["seat"].int_value(), false);
        std::cout << json11::Json(moves).dump() << std::endl;
    }
} catch (const std::exception& error) {
    std::cerr << "akochan exception: " << error.what() << std::endl;
    return 4;
}
