module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire local_req,
    input wire edn_req,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (local_req && edn_req) |=> local_req);

endmodule


bind keymgr_reseed_ctrl sva_checker sva_checker_inst (.*);
